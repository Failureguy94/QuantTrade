"""API tests for backtest experiments."""

import uuid
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting.engine import ENGINE_VERSION
from app.market_data.exceptions import ProviderConnectionError
from app.models.market_data import MarketDataCandle
from app.models.research import Experiment, SimulationJob
from helpers import FakeProvider, make_bars, wave_closes

BASE = "/api/v1/experiments"
STRATEGIES = "/api/v1/strategies"
LAST_BAR = "2024-10-04"  # 200 weekdays from Monday 2024-01-01


async def make_strategy_version(
    client: AsyncClient, headers: dict, strategy_type="SMA_CROSSOVER", parameters=None
) -> str:
    response = await client.post(
        STRATEGIES,
        json={
            "name": "Backtest subject",
            "strategy_type": strategy_type,
            "parameters": (
                {"fast_window": 5, "slow_window": 20} if parameters is None else parameters
            ),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["latest_version"]["id"]


def backtest_payload(version_id: str, instrument_id, **overrides) -> dict:
    return {
        "strategy_version_id": version_id,
        "instrument_id": str(instrument_id),
        "start": "2024-01-01",
        "end": LAST_BAR,
        "initial_capital": "100000",
        **overrides,
    }


async def test_backtest_runs_and_is_persisted(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    db_session: AsyncSession,
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers)

    response = await async_client.post(
        f"{BASE}/backtests", json=backtest_payload(version_id, aapl.id), headers=auth_headers
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "COMPLETED"
    assert body["experiment_type"] == "BACKTEST"
    assert body["error_message"] is None
    assert body["engine_version"] == ENGINE_VERSION
    assert body["strategy_version_id"] == version_id
    assert len(body["data_fingerprint"]) == 64  # sha-256 of the bars used
    assert body["started_at"] <= body["completed_at"]

    # Everything needed to reproduce the run is recorded (ADR-007).
    configuration = body["configuration"]
    assert configuration["strategy"]["type"] == "SMA_CROSSOVER"
    assert configuration["strategy"]["parameters"] == {"fast_window": 5, "slow_window": 20}
    assert configuration["strategy"]["version"] == 1
    assert configuration["instrument"]["symbol"] == "AAPL"
    assert configuration["request"]["commission_bps"] == "5"
    assert configuration["execution"]["adjustment_mode"] == "SPLIT_AND_DIVIDEND"

    results = body["results"]
    assert results["data"]["bars"] == 200
    assert len(results["equity_curve"]) == 200
    assert Decimal(results["equity_curve"][0]["equity"]) == Decimal("100000")
    assert results["final_equity"] == results["equity_curve"][-1]["equity"]
    assert results["metrics"]["closed_trades"] >= 1
    assert results["metrics"]["periods_per_year"] == 252
    assert body["metrics"] == results["metrics"]

    # The bars were validated and cached, and a job record tracks the run.
    cached = await db_session.scalar(
        select(func.count()).select_from(MarketDataCandle).where(
            MarketDataCandle.instrument_id == aapl.id
        )
    )
    assert cached == 200
    job = (
        await db_session.execute(
            select(SimulationJob).where(SimulationJob.experiment_id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert (job.status, job.progress) == ("COMPLETED", 100)
    assert job.result_reference == f"experiments/{body['id']}"


async def test_backtests_are_reproducible(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers)
    payload = backtest_payload(version_id, aapl.id, allow_short=True)

    first = (
        await async_client.post(f"{BASE}/backtests", json=payload, headers=auth_headers)
    ).json()
    calls_after_first = fake_provider.calls
    second = (
        await async_client.post(f"{BASE}/backtests", json=payload, headers=auth_headers)
    ).json()

    assert first["id"] != second["id"]
    assert first["data_fingerprint"] == second["data_fingerprint"]
    assert first["results"]["equity_curve"] == second["results"]["equity_curve"]
    assert first["results"]["trades"] == second["results"]["trades"]
    # The second run was served from the candle store, not the provider.
    assert fake_provider.calls == calls_after_first

    # Different data must change the fingerprint.
    msft = instruments["XNAS:MSFT"]
    fake_provider.history["MSFT"] = make_bars(wave_closes(200, base=250.0))
    other = (
        await async_client.post(
            f"{BASE}/backtests",
            json=backtest_payload(version_id, msft.id, allow_short=True),
            headers=auth_headers,
        )
    ).json()
    assert other["status"] == "COMPLETED"
    assert other["data_fingerprint"] != first["data_fingerprint"]


async def test_backtest_respects_date_range_and_costs(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers, "BUY_AND_HOLD", {})

    async def run(**overrides) -> dict:
        response = await async_client.post(
            f"{BASE}/backtests",
            json=backtest_payload(version_id, aapl.id, **overrides),
            headers=auth_headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    january = await run(end="2024-01-31")
    assert january["results"]["data"]["bars"] == 23  # weekdays in January 2024
    assert january["results"]["data"]["last_bar"].startswith("2024-01-31")

    # January is now cached. A wider request must not be answered from that
    # partial cache: the missing months are fetched.
    full_year = await run()
    assert full_year["results"]["data"]["bars"] == 200
    assert full_year["results"]["data"]["last_bar"].startswith(LAST_BAR)
    assert full_year["results"]["warnings"] == []

    free = await run(commission_bps="0", slippage_bps="0")
    costly = await run(commission_bps="50", slippage_bps="50")
    assert Decimal(free["results"]["total_commission"]) == 0
    assert Decimal(costly["results"]["total_commission"]) > 0
    assert Decimal(costly["results"]["final_equity"]) < Decimal(free["results"]["final_equity"])

    # With no costs, buy-and-hold tracks the instrument from the first fill onward.
    curve = free["results"]["equity_curve"]
    assert Decimal(curve[1]["position"]) > 0
    assert free["results"]["metrics"]["open_trades"] == 1


async def test_backtest_warns_when_data_does_not_span_the_request(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))  # 2024-01-01 .. 2024-10-04
    version_id = await make_strategy_version(async_client, auth_headers, "BUY_AND_HOLD", {})

    response = await async_client.post(
        f"{BASE}/backtests",
        json=backtest_payload(version_id, aapl.id, start="2023-01-01", end="2024-12-31"),
        headers=auth_headers,
    )
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["results"]["data"]["bars"] == 200
    assert body["results"]["warnings"] == [
        "Data begins on 2024-01-01, after the requested start 2023-01-01",
        "Data ends on 2024-10-04, before the requested end 2024-12-31",
    ]


async def test_zero_amounts_use_plain_decimal_notation(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers, "BUY_AND_HOLD", {})
    body = (
        await async_client.post(
            f"{BASE}/backtests",
            json=backtest_payload(version_id, aapl.id, commission_bps="0", slippage_bps="0"),
            headers=auth_headers,
        )
    ).json()
    assert body["results"]["total_commission"] == "0.00000000"
    assert body["results"]["equity_curve"][0]["position"] == "0"
    assert "E" not in body["results"]["final_equity"]


async def test_failed_run_is_recorded_not_raised(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    db_session: AsyncSession,
):
    aapl = instruments["XNAS:AAPL"]
    version_id = await make_strategy_version(async_client, auth_headers)

    # No data for the instrument.
    response = await async_client.post(
        f"{BASE}/backtests", json=backtest_payload(version_id, aapl.id), headers=auth_headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    assert "No market data" in body["error_message"]
    assert body["results"] is None and body["metrics"] is None
    assert body["completed_at"] is not None

    job = (
        await db_session.execute(
            select(SimulationJob).where(SimulationJob.experiment_id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert job.status == "FAILED"
    assert job.error_message == body["error_message"]

    # Provider outage.
    fake_provider.error = ProviderConnectionError("upstream down", provider="YAHOO")
    outage = await async_client.post(
        f"{BASE}/backtests", json=backtest_payload(version_id, aapl.id), headers=auth_headers
    )
    assert outage.json()["status"] == "FAILED"
    assert "upstream down" in outage.json()["error_message"]

    # A single bar cannot be simulated.
    fake_provider.error = None
    fake_provider.history["AAPL"] = make_bars([100.0])
    short = await async_client.post(
        f"{BASE}/backtests", json=backtest_payload(version_id, aapl.id), headers=auth_headers
    )
    assert short.json()["status"] == "FAILED"
    assert "At least two bars" in short.json()["error_message"]


async def test_backtest_request_validation(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    aapl = instruments["XNAS:AAPL"]
    version_id = await make_strategy_version(async_client, auth_headers)

    invalid_bodies = [
        backtest_payload(version_id, aapl.id, start="2024-06-01", end="2024-01-01"),
        backtest_payload(version_id, aapl.id, initial_capital="0"),
        backtest_payload(version_id, aapl.id, interval="1m"),
        backtest_payload(version_id, aapl.id, position_size="1.5"),
        backtest_payload(version_id, aapl.id, commission_bps="-1"),
    ]
    for body in invalid_bodies:
        response = await async_client.post(f"{BASE}/backtests", json=body, headers=auth_headers)
        assert response.status_code == 422, body

    unknown_instrument = await async_client.post(
        f"{BASE}/backtests",
        json=backtest_payload(version_id, uuid.uuid4()),
        headers=auth_headers,
    )
    assert unknown_instrument.status_code == 404
    assert unknown_instrument.json()["detail"] == "Instrument not found"

    unknown_version = await async_client.post(
        f"{BASE}/backtests",
        json=backtest_payload(str(uuid.uuid4()), aapl.id),
        headers=auth_headers,
    )
    assert unknown_version.status_code == 404


async def test_experiments_are_private_to_their_owner(
    async_client: AsyncClient,
    make_user,
    instruments,
    fake_provider: FakeProvider,
    db_session: AsyncSession,
):
    _, alice = await make_user()
    _, bob = await make_user()
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(120))
    version_id = await make_strategy_version(async_client, alice)

    # Bob cannot backtest Alice's strategy version, and nothing is recorded for the attempt.
    stolen = await async_client.post(
        f"{BASE}/backtests", json=backtest_payload(version_id, aapl.id), headers=bob
    )
    assert stolen.status_code == 404
    assert await db_session.scalar(select(func.count()).select_from(Experiment)) == 0

    created = await async_client.post(
        f"{BASE}/backtests", json=backtest_payload(version_id, aapl.id), headers=alice
    )
    experiment_id = created.json()["id"]

    assert (await async_client.get(BASE, headers=bob)).json() == []
    for method, url in [
        ("GET", f"{BASE}/{experiment_id}"),
        ("GET", f"{BASE}/{experiment_id}/jobs"),
        ("DELETE", f"{BASE}/{experiment_id}"),
    ]:
        assert (await async_client.request(method, url, headers=bob)).status_code == 404
    assert (await async_client.get(f"{BASE}/{experiment_id}")).status_code == 401


async def test_list_get_jobs_and_delete(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    aapl = instruments["XNAS:AAPL"]
    msft = instruments["XNAS:MSFT"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(120))
    version_id = await make_strategy_version(async_client, auth_headers)

    completed = (
        await async_client.post(
            f"{BASE}/backtests", json=backtest_payload(version_id, aapl.id), headers=auth_headers
        )
    ).json()
    failed = (
        await async_client.post(
            f"{BASE}/backtests", json=backtest_payload(version_id, msft.id), headers=auth_headers
        )
    ).json()
    assert (completed["status"], failed["status"]) == ("COMPLETED", "FAILED")

    listed = (await async_client.get(BASE, headers=auth_headers)).json()
    assert {item["id"] for item in listed} == {completed["id"], failed["id"]}
    # The list view carries metrics but not the bulky equity curve.
    assert all("results" not in item for item in listed)
    by_id = {item["id"]: item for item in listed}
    assert by_id[completed["id"]]["metrics"]["bars"] == 120
    assert by_id[failed["id"]]["metrics"] is None

    only_failed = (await async_client.get(f"{BASE}?status=failed", headers=auth_headers)).json()
    assert [item["id"] for item in only_failed] == [failed["id"]]
    by_version = (
        await async_client.get(f"{BASE}?strategy_version_id={version_id}", headers=auth_headers)
    ).json()
    assert len(by_version) == 2

    detail = (await async_client.get(f"{BASE}/{completed['id']}", headers=auth_headers)).json()
    assert detail["results"]["equity_curve"] == completed["results"]["equity_curve"]

    jobs = (
        await async_client.get(f"{BASE}/{completed['id']}/jobs", headers=auth_headers)
    ).json()
    assert len(jobs) == 1 and jobs[0]["status"] == "COMPLETED"

    deleted = await async_client.delete(f"{BASE}/{completed['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert (
        await async_client.get(f"{BASE}/{completed['id']}", headers=auth_headers)
    ).status_code == 404
