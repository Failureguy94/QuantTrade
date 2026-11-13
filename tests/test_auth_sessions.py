"""Sessions and account lifecycle: refresh tokens, email links, lockout, deletion."""

import re
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email import Email
from app.models.identity import EmailToken, RefreshToken, User
from app.models.portfolio import LedgerTransaction, Order, Portfolio, Position, Trade
from app.models.research import Experiment, RiskReport, SimulationJob, Strategy, StrategyVersion
from helpers import FakeProvider, make_bars, wave_closes

AUTH = "/api/v1/auth"
PASSWORD = "CorrectHorse1!"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_in(email: Email) -> str:
    match = re.search(r"token=([\w-]+)", email.body)
    assert match, email.body
    return match.group(1)


async def register(client: AsyncClient, email: str = "trader@quant.example") -> dict:
    response = await client.post(f"{AUTH}/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


async def login(client: AsyncClient, email: str = "trader@quant.example", password=PASSWORD):
    return await client.post(f"{AUTH}/login", json={"email": email, "password": password})


async def signed_in(client: AsyncClient, email: str = "trader@quant.example") -> dict:
    await register(client, email)
    response = await login(client, email)
    assert response.status_code == 200, response.text
    return response.json()


# ----------------------------------------------------------------- token pairs
async def test_login_returns_access_and_refresh_tokens(
    async_client: AsyncClient, db_session: AsyncSession
):
    tokens = await signed_in(async_client)
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == settings.access_token_minutes * 60
    assert len(tokens["refresh_token"]) >= 40

    me = await async_client.get(f"{AUTH}/me", headers=bearer(tokens["access_token"]))
    assert me.status_code == 200

    # Only the hash of the refresh token is stored.
    stored = (await db_session.execute(select(RefreshToken))).scalar_one()
    assert stored.token_hash != tokens["refresh_token"]
    assert len(stored.token_hash) == 64
    assert stored.expires_at > datetime.now(UTC) + timedelta(days=settings.refresh_token_days - 1)


async def test_refresh_rotates_the_token(async_client: AsyncClient, db_session: AsyncSession):
    tokens = await signed_in(async_client)
    refreshed = await async_client.post(
        f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    new = refreshed.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    me = await async_client.get(f"{AUTH}/me", headers=bearer(new["access_token"]))
    assert me.status_code == 200

    rows = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(rows) == 2
    assert len({row.family_id for row in rows}) == 1  # same session
    assert sum(row.rotated_at is not None for row in rows) == 1


async def test_a_just_rotated_token_still_works_within_the_grace_period(
    async_client: AsyncClient,
):
    """Two requests of one client racing to refresh must not log the user out."""
    tokens = await signed_in(async_client)
    body = {"refresh_token": tokens["refresh_token"]}
    first = await async_client.post(f"{AUTH}/refresh", json=body)
    second = await async_client.post(f"{AUTH}/refresh", json=body)
    assert first.status_code == 200 and second.status_code == 200
    for response in (first, second):
        follow_up = await async_client.post(
            f"{AUTH}/refresh", json={"refresh_token": response.json()["refresh_token"]}
        )
        assert follow_up.status_code == 200


async def test_reusing_a_retired_token_ends_the_whole_session(
    async_client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "refresh_token_grace_seconds", 0)
    tokens = await signed_in(async_client)
    stolen = tokens["refresh_token"]
    current = (await async_client.post(f"{AUTH}/refresh", json={"refresh_token": stolen})).json()

    replay = await async_client.post(f"{AUTH}/refresh", json={"refresh_token": stolen})
    assert replay.status_code == 401
    assert replay.json()["code"] == "invalid_refresh_token"
    assert replay.headers["WWW-Authenticate"] == "Bearer"

    # The legitimate holder's newer token is revoked too: the session is over.
    after = await async_client.post(
        f"{AUTH}/refresh", json={"refresh_token": current["refresh_token"]}
    )
    assert after.status_code == 401


async def test_reuse_only_ends_its_own_session(async_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "refresh_token_grace_seconds", 0)
    laptop = await signed_in(async_client)
    phone = (await login(async_client)).json()

    await async_client.post(f"{AUTH}/refresh", json={"refresh_token": laptop["refresh_token"]})
    await async_client.post(f"{AUTH}/refresh", json={"refresh_token": laptop["refresh_token"]})

    still_fine = await async_client.post(
        f"{AUTH}/refresh", json={"refresh_token": phone["refresh_token"]}
    )
    assert still_fine.status_code == 200


async def test_unknown_and_expired_refresh_tokens_are_refused(
    async_client: AsyncClient, db_session: AsyncSession
):
    unknown = await async_client.post(f"{AUTH}/refresh", json={"refresh_token": "x" * 64})
    assert unknown.status_code == 401

    tokens = await signed_in(async_client)
    await db_session.execute(
        update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    expired = await async_client.post(
        f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert expired.status_code == 401


async def test_refresh_is_refused_for_a_deactivated_user(
    async_client: AsyncClient, db_session: AsyncSession
):
    tokens = await signed_in(async_client)
    await db_session.execute(update(User).values(is_active=False))
    response = await async_client.post(
        f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401


async def test_logout_revokes_the_session_and_is_idempotent(async_client: AsyncClient):
    tokens = await signed_in(async_client)
    body = {"refresh_token": tokens["refresh_token"]}
    assert (await async_client.post(f"{AUTH}/logout", json=body)).status_code == 204
    assert (await async_client.post(f"{AUTH}/logout", json=body)).status_code == 204
    assert (await async_client.post(f"{AUTH}/refresh", json=body)).status_code == 401
    # A token nobody issued is ignored rather than reported.
    assert (
        await async_client.post(f"{AUTH}/logout", json={"refresh_token": "y" * 64})
    ).status_code == 204


async def test_logout_all_ends_every_session_and_access_token(async_client: AsyncClient):
    laptop = await signed_in(async_client)
    phone = (await login(async_client)).json()

    response = await async_client.post(f"{AUTH}/logout-all", headers=bearer(phone["access_token"]))
    assert response.status_code == 204

    for session in (laptop, phone):
        me = await async_client.get(f"{AUTH}/me", headers=bearer(session["access_token"]))
        assert me.status_code == 401
        assert me.json()["detail"] == "Token has been revoked"
        refresh = await async_client.post(
            f"{AUTH}/refresh", json={"refresh_token": session["refresh_token"]}
        )
        assert refresh.status_code == 401

    # Signing in again works and the new token is accepted.
    fresh = (await login(async_client)).json()
    assert (
        await async_client.get(f"{AUTH}/me", headers=bearer(fresh["access_token"]))
    ).status_code == 200


# ------------------------------------------------------------------- passwords
async def test_change_password(async_client: AsyncClient):
    old = await signed_in(async_client)
    other_device = (await login(async_client)).json()

    wrong = await async_client.post(
        f"{AUTH}/change-password",
        json={"current_password": "NotMyPassword1!", "new_password": "BrandNewPass2!"},
        headers=bearer(old["access_token"]),
    )
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "wrong_password"

    weak = await async_client.post(
        f"{AUTH}/change-password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=bearer(old["access_token"]),
    )
    assert weak.status_code == 422

    changed = await async_client.post(
        f"{AUTH}/change-password",
        json={"current_password": PASSWORD, "new_password": "BrandNewPass2!"},
        headers=bearer(old["access_token"]),
    )
    assert changed.status_code == 200, changed.text
    new = changed.json()

    # The session that changed the password continues on its new tokens; every
    # older token, on this device and others, is dead.
    assert (
        await async_client.get(f"{AUTH}/me", headers=bearer(new["access_token"]))
    ).status_code == 200
    for stale in (old, other_device):
        assert (
            await async_client.get(f"{AUTH}/me", headers=bearer(stale["access_token"]))
        ).status_code == 401
        assert (
            await async_client.post(
                f"{AUTH}/refresh", json={"refresh_token": stale["refresh_token"]}
            )
        ).status_code == 401

    assert (await login(async_client)).status_code == 401
    assert (await login(async_client, password="BrandNewPass2!")).status_code == 200


async def test_password_reset_flow(async_client: AsyncClient, sent_emails: list[Email]):
    old = await signed_in(async_client)
    sent_emails.clear()

    requested = await async_client.post(
        f"{AUTH}/forgot-password", json={"email": "Trader@Quant.Example"}
    )
    assert requested.status_code == 202
    assert len(sent_emails) == 1
    assert sent_emails[0].to == "trader@quant.example"
    assert f"{settings.frontend_base_url}/reset-password?token=" in sent_emails[0].body
    token = token_in(sent_emails[0])

    weak = await async_client.post(
        f"{AUTH}/reset-password", json={"token": token, "new_password": "short"}
    )
    assert weak.status_code == 422

    reset = await async_client.post(
        f"{AUTH}/reset-password", json={"token": token, "new_password": "ResetPassword3!"}
    )
    assert reset.status_code == 204

    # Old sessions are over, the old password is gone, the link cannot be replayed.
    assert (
        await async_client.get(f"{AUTH}/me", headers=bearer(old["access_token"]))
    ).status_code == 401
    assert (await login(async_client)).status_code == 401
    assert (await login(async_client, password="ResetPassword3!")).status_code == 200
    replay = await async_client.post(
        f"{AUTH}/reset-password", json={"token": token, "new_password": "AnotherPass4!"}
    )
    assert replay.status_code == 422
    assert replay.json()["code"] == "invalid_token"


async def test_forgot_password_does_not_reveal_whether_an_account_exists(
    async_client: AsyncClient, sent_emails: list[Email]
):
    await register(async_client)
    sent_emails.clear()
    known = await async_client.post(
        f"{AUTH}/forgot-password", json={"email": "trader@quant.example"}
    )
    unknown = await async_client.post(
        f"{AUTH}/forgot-password", json={"email": "stranger@quant.example"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [email.to for email in sent_emails] == ["trader@quant.example"]


async def test_a_new_reset_link_replaces_the_previous_one(
    async_client: AsyncClient, sent_emails: list[Email]
):
    await register(async_client)
    sent_emails.clear()
    for _ in range(2):
        await async_client.post(f"{AUTH}/forgot-password", json={"email": "trader@quant.example"})
    first, second = (token_in(email) for email in sent_emails)

    stale = await async_client.post(
        f"{AUTH}/reset-password", json={"token": first, "new_password": "ResetPassword3!"}
    )
    assert stale.status_code == 422
    fresh = await async_client.post(
        f"{AUTH}/reset-password", json={"token": second, "new_password": "ResetPassword3!"}
    )
    assert fresh.status_code == 204


async def test_expired_reset_link_is_refused(
    async_client: AsyncClient, db_session: AsyncSession, sent_emails: list[Email]
):
    await register(async_client)
    sent_emails.clear()
    await async_client.post(f"{AUTH}/forgot-password", json={"email": "trader@quant.example"})
    await db_session.execute(
        update(EmailToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    response = await async_client.post(
        f"{AUTH}/reset-password",
        json={"token": token_in(sent_emails[0]), "new_password": "ResetPassword3!"},
    )
    assert response.status_code == 422


async def test_a_verification_token_cannot_reset_a_password(
    async_client: AsyncClient, sent_emails: list[Email]
):
    await register(async_client)
    verification_token = token_in(sent_emails[0])
    response = await async_client.post(
        f"{AUTH}/reset-password",
        json={"token": verification_token, "new_password": "ResetPassword3!"},
    )
    assert response.status_code == 422
    assert (await login(async_client)).status_code == 200


# ---------------------------------------------------------- email verification
async def test_registration_sends_a_verification_link(
    async_client: AsyncClient, sent_emails: list[Email]
):
    user = await register(async_client)
    assert user["email_verified"] is False
    assert len(sent_emails) == 1
    assert sent_emails[0].to == "trader@quant.example"
    assert f"{settings.frontend_base_url}/verify-email?token=" in sent_emails[0].body

    verified = await async_client.post(
        f"{AUTH}/verify-email", json={"token": token_in(sent_emails[0])}
    )
    assert verified.status_code == 200
    assert verified.json()["email_verified"] is True

    replay = await async_client.post(
        f"{AUTH}/verify-email", json={"token": token_in(sent_emails[0])}
    )
    assert replay.status_code == 422


async def test_sign_in_can_require_a_verified_email(
    async_client: AsyncClient, sent_emails: list[Email], monkeypatch
):
    monkeypatch.setattr(settings, "require_email_verification", True)
    await register(async_client)

    blocked = await login(async_client)
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "email_not_verified"
    # A wrong password still gets the generic error, not a hint about verification.
    assert (await login(async_client, password="WrongPassword9!")).status_code == 401

    await async_client.post(f"{AUTH}/verify-email", json={"token": token_in(sent_emails[0])})
    assert (await login(async_client)).status_code == 200


async def test_resend_verification(async_client: AsyncClient, sent_emails: list[Email]):
    await register(async_client)
    resent = await async_client.post(
        f"{AUTH}/resend-verification", json={"email": "trader@quant.example"}
    )
    assert resent.status_code == 202
    assert len(sent_emails) == 2
    first, second = (token_in(email) for email in sent_emails)
    assert first != second

    # Only the newest link works.
    assert (
        await async_client.post(f"{AUTH}/verify-email", json={"token": first})
    ).status_code == 422
    assert (
        await async_client.post(f"{AUTH}/verify-email", json={"token": second})
    ).status_code == 200

    # Nothing more is sent once verified, or for unknown addresses; same answer.
    again = await async_client.post(
        f"{AUTH}/resend-verification", json={"email": "trader@quant.example"}
    )
    unknown = await async_client.post(
        f"{AUTH}/resend-verification", json={"email": "stranger@quant.example"}
    )
    assert again.json() == unknown.json() == resent.json()
    assert len(sent_emails) == 2


async def test_registration_succeeds_even_if_the_email_cannot_be_sent(async_client: AsyncClient):
    from app.api.deps import get_email_sender
    from app.main import app

    class Broken:
        async def send(self, email: Email) -> None:
            raise ConnectionError("mail server is down")

    app.dependency_overrides[get_email_sender] = lambda: Broken()
    user = await register(async_client)
    assert user["email"] == "trader@quant.example"
    assert (await login(async_client)).status_code == 200


# ---------------------------------------------------------------------- lockout
async def test_repeated_failures_lock_sign_in_for_that_email(async_client: AsyncClient):
    await register(async_client)
    for _ in range(settings.login_max_failures):
        assert (await login(async_client, password="WrongPassword9!")).status_code == 401

    locked = await login(async_client)  # even the right password now waits
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 60
    assert "Too many failed sign-in attempts" in locked.json()["detail"]

    # Another account is unaffected.
    await register(async_client, "other@quant.example")
    assert (await login(async_client, "other@quant.example")).status_code == 200


async def test_lockout_is_the_same_for_emails_without_an_account(async_client: AsyncClient):
    for _ in range(settings.login_max_failures):
        response = await login(async_client, "ghost@quant.example", "WrongPassword9!")
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect email or password"
    assert (await login(async_client, "ghost@quant.example", "x")).status_code == 429


async def test_a_successful_sign_in_resets_the_failure_count(async_client: AsyncClient):
    await register(async_client)
    for _ in range(settings.login_max_failures - 1):
        await login(async_client, password="WrongPassword9!")
    assert (await login(async_client)).status_code == 200
    for _ in range(settings.login_max_failures - 1):
        assert (await login(async_client, password="WrongPassword9!")).status_code == 401
    assert (await login(async_client)).status_code == 200


async def test_resetting_the_password_lifts_the_lockout(
    async_client: AsyncClient, sent_emails: list[Email]
):
    await register(async_client)
    for _ in range(settings.login_max_failures):
        await login(async_client, password="WrongPassword9!")
    assert (await login(async_client)).status_code == 429

    sent_emails.clear()
    await async_client.post(f"{AUTH}/forgot-password", json={"email": "trader@quant.example"})
    await async_client.post(
        f"{AUTH}/reset-password",
        json={"token": token_in(sent_emails[0]), "new_password": "ResetPassword3!"},
    )
    assert (await login(async_client, password="ResetPassword3!")).status_code == 200


# --------------------------------------------------------------------- deletion
async def test_delete_account_erases_everything_the_user_owns(
    async_client: AsyncClient,
    db_session: AsyncSession,
    instruments,
    fake_provider: FakeProvider,
    make_user,
):
    tokens = await signed_in(async_client)
    headers = bearer(tokens["access_token"])
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    fake_provider.set_quote("AAPL", "200")

    # A bystander whose data must survive.
    bystander, bystander_headers = await make_user()
    kept = await async_client.post(
        "/api/v1/strategies",
        json={"name": "Keep me", "strategy_type": "BUY_AND_HOLD", "parameters": {}},
        headers=bystander_headers,
    )
    assert kept.status_code == 201

    strategy = (
        await async_client.post(
            "/api/v1/strategies",
            json={"name": "Mine", "strategy_type": "SMA_CROSSOVER", "parameters": {}},
            headers=headers,
        )
    ).json()
    experiment = (
        await async_client.post(
            "/api/v1/experiments/backtests",
            json={
                "strategy_version_id": strategy["latest_version"]["id"],
                "instrument_id": str(aapl.id),
                "start": "2024-01-01",
                "end": "2024-10-04",
            },
            headers=headers,
        )
    ).json()
    assert experiment["status"] == "COMPLETED", experiment
    portfolio = (
        await async_client.post(
            "/api/v1/portfolios",
            json={"name": "Paper", "base_currency": "USD", "initial_capital": "100000"},
            headers=headers,
        )
    ).json()
    order = await async_client.post(
        f"/api/v1/portfolios/{portfolio['id']}/orders",
        json={"instrument_id": str(aapl.id), "side": "BUY", "quantity": "10"},
        headers=headers,
    )
    assert order.json()["status"] == "FILLED", order.text
    allocations = await async_client.put(
        f"/api/v1/portfolios/{portfolio['id']}/allocations",
        json={"allocations": [{"strategy_id": strategy["id"], "allocation_weight": "1"}]},
        headers=headers,
    )
    assert allocations.status_code == 200, allocations.text
    report = await async_client.post(
        "/api/v1/risk/reports", json={"experiment_id": experiment["id"]}, headers=headers
    )
    assert report.status_code == 201, report.text

    wrong = await async_client.post(
        f"{AUTH}/delete-account", json={"password": "NotMyPassword1!"}, headers=headers
    )
    assert wrong.status_code == 422
    assert (await async_client.get(f"{AUTH}/me", headers=headers)).status_code == 200

    deleted = await async_client.post(
        f"{AUTH}/delete-account", json={"password": PASSWORD}, headers=headers
    )
    assert deleted.status_code == 204, deleted.text

    async def count(model) -> int:
        return await db_session.scalar(select(func.count()).select_from(model))

    assert await db_session.scalar(
        select(func.count()).select_from(User).where(User.email == "trader@quant.example")
    ) == 0
    for model in (
        Portfolio, Order, Trade, Position, LedgerTransaction,
        Experiment, SimulationJob, RiskReport, RefreshToken, EmailToken,
    ):
        assert await count(model) == 0, model.__name__
    # Only the bystander's rows remain.
    assert await count(Strategy) == 1
    assert await count(StrategyVersion) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(User).where(User.id == bystander.id)
    ) == 1

    # The account is gone: its token is dead and the email can register again.
    assert (await async_client.get(f"{AUTH}/me", headers=headers)).status_code == 401
    assert (await login(async_client)).status_code == 401
    await register(async_client)
