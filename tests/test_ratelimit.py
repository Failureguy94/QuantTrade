"""Rate limits, run concurrency and the request body cap."""

import uuid

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.ratelimit import ConcurrencyLimiter, RateLimitExceeded, SlidingWindowLimiter

API = "/api/v1"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ------------------------------------------------------------------ limiter unit
def test_limiter_allows_the_limit_then_refuses_with_the_wait():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock)
    assert [limiter.hit("k", 3, 60) for _ in range(3)] == [0.0, 0.0, 0.0]
    clock.now += 10
    assert limiter.hit("k", 3, 60) == pytest.approx(50.0)


def test_limiter_window_slides():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock)
    limiter.hit("k", 2, 60)
    clock.now += 30
    limiter.hit("k", 2, 60)
    assert limiter.hit("k", 2, 60) == pytest.approx(30.0)
    clock.now += 30  # the first event leaves the window, the second is still in it
    assert limiter.hit("k", 2, 60) == 0.0
    assert limiter.hit("k", 2, 60) == pytest.approx(30.0)


def test_limiter_keys_are_independent_and_refusals_are_not_counted():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock)
    assert limiter.hit("a", 1, 60) == 0.0
    for _ in range(5):
        assert limiter.hit("a", 1, 60) > 0  # refused attempts must not extend the wait
    assert limiter.hit("b", 1, 60) == 0.0
    clock.now += 60
    assert limiter.hit("a", 1, 60) == 0.0


def test_limiter_clear_and_peek():
    limiter = SlidingWindowLimiter(FakeClock())
    limiter.record("k")
    limiter.record("k")
    assert limiter.retry_after("k", 2, 60) > 0
    assert limiter.retry_after("k", 3, 60) == 0.0  # peeking does not record
    assert limiter.retry_after("k", 3, 60) == 0.0
    limiter.clear("k")
    assert limiter.retry_after("k", 1, 60) == 0.0


def test_rate_limit_error_rounds_retry_after_up():
    assert RateLimitExceeded(0.2).headers == {"Retry-After": "1"}
    assert RateLimitExceeded(12.01).headers == {"Retry-After": "13"}


async def test_concurrency_limiter_caps_and_releases():
    slots = ConcurrencyLimiter()
    async with slots.slot("u", 2):
        async with slots.slot("u", 2):
            with pytest.raises(RateLimitExceeded):
                async with slots.slot("u", 2):
                    pass
            async with slots.slot("other", 2):
                pass
    async with slots.slot("u", 2):  # both released
        pass


async def test_concurrency_slot_is_released_when_the_work_fails():
    slots = ConcurrencyLimiter()
    with pytest.raises(RuntimeError):
        async with slots.slot("u", 1):
            raise RuntimeError("boom")
    async with slots.slot("u", 1):
        pass


# --------------------------------------------------------------------- endpoints
async def test_auth_routes_are_limited_per_client_address(async_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 3)
    body = {"email": "nobody@quant.example", "password": "WrongPassword1!"}
    for _ in range(3):
        assert (await async_client.post(f"{API}/auth/login", json=body)).status_code == 401

    blocked = await async_client.post(f"{API}/auth/login", json=body)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1
    # The limit is shared by the unauthenticated account routes.
    register = await async_client.post(
        f"{API}/auth/register", json={"email": "new@quant.example", "password": "Password123!"}
    )
    assert register.status_code == 429


async def test_backtests_are_limited_per_user(async_client: AsyncClient, make_user, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_backtests_per_minute", 2)
    _, first = await make_user()
    _, second = await make_user()
    body = {
        "strategy_version_id": str(uuid.uuid4()),
        "instrument_id": str(uuid.uuid4()),
        "start": "2024-01-01",
        "end": "2024-06-01",
    }
    url = f"{API}/experiments/backtests"
    assert (await async_client.post(url, json=body, headers=first)).status_code == 404
    assert (await async_client.post(url, json=body, headers=first)).status_code == 404
    blocked = await async_client.post(url, json=body, headers=first)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    # Another user has their own allowance.
    assert (await async_client.post(url, json=body, headers=second)).status_code == 404


async def test_risk_reports_are_limited_per_user(
    async_client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "rate_limit_risk_per_minute", 1)
    body = {"portfolio_id": str(uuid.uuid4())}
    url = f"{API}/risk/reports"
    assert (await async_client.post(url, json=body, headers=auth_headers)).status_code == 404
    assert (await async_client.post(url, json=body, headers=auth_headers)).status_code == 429


async def test_historical_market_data_is_limited_per_user(
    async_client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "rate_limit_market_data_per_minute", 2)
    url = f"{API}/market-data/{uuid.uuid4()}/historical"
    assert (await async_client.get(url, headers=auth_headers)).status_code == 404
    assert (await async_client.get(url, headers=auth_headers)).status_code == 404
    assert (await async_client.get(url, headers=auth_headers)).status_code == 429


async def test_anonymous_requests_do_not_consume_a_users_allowance(
    async_client: AsyncClient, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "rate_limit_market_data_per_minute", 1)
    url = f"{API}/market-data/{uuid.uuid4()}/historical"
    for _ in range(3):
        assert (await async_client.get(url)).status_code == 401
    assert (await async_client.get(url, headers=auth_headers)).status_code == 404


async def test_limits_can_be_switched_off(async_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 1)
    body = {"email": "nobody@quant.example", "password": "WrongPassword1!"}
    for _ in range(8):  # beyond both the route limit and the failed-login lockout
        assert (await async_client.post(f"{API}/auth/login", json=body)).status_code == 401


# --------------------------------------------------------------------- body size
async def test_oversized_declared_body_is_refused(async_client: AsyncClient):
    payload = b'{"email": "a@quant.example", "password": "' + b"x" * 1_100_000 + b'"}'
    response = await async_client.post(
        f"{API}/auth/login", content=payload, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large", "code": "payload_too_large"}


async def test_oversized_streamed_body_is_cut_off(async_client: AsyncClient):
    async def chunks():
        yield b'{"email": "a@quant.example", "password": "'
        for _ in range(12):
            yield b"x" * 100_000
        yield b'"}'

    response = await async_client.post(
        f"{API}/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


async def test_normal_bodies_pass(async_client: AsyncClient):
    response = await async_client.post(
        f"{API}/auth/login", json={"email": "a@quant.example", "password": "x" * 100}
    )
    assert response.status_code == 401
