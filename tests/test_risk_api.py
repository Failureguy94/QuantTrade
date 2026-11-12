"""API tests for portfolio and experiment risk reports."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from httpx import AsyncClient

from app.market_data.exceptions import ProviderConnectionError
from helpers import FakeProvider, make_bars, wave_closes

BASE = "/api/v1/risk/reports"
PORTFOLIOS = "/api/v1/portfolios"
D = Decimal


def random_walk(count: int, seed: int, start: float = 100.0) -> list[float]:
    rng = np.random.default_rng(seed)
    return (start * np.cumprod(1 + rng.normal(0.0004, 0.015, count))).round(4).tolist()


def recent_bars(closes: list[float]):
    """Daily bars ending on the most recent weekday."""
    return make_bars(closes, end=datetime.now(UTC).date())


async def buy(client, headers, portfolio_id, instrument, quantity: str) -> None:
    response = await client.post(
        f"{PORTFOLIOS}/{portfolio_id}/orders",
        json={"instrument_id": str(instrument.id), "side": "BUY", "quantity": quantity},
        headers=headers,
    )
    assert response.json()["status"] == "FILLED", response.text


@pytest.fixture
async def funded(async_client: AsyncClient, auth_headers, instruments, fake_provider):
    """USD portfolio holding AAPL and MSFT, each with ~150 sessions of price history."""
    aapl, msft = instruments["XNAS:AAPL"], instruments["XNAS:MSFT"]
    histories = {"AAPL": random_walk(150, seed=1), "MSFT": random_walk(150, seed=2, start=300)}
    for symbol, closes in histories.items():
        fake_provider.history[symbol] = recent_bars(closes)
        fake_provider.set_quote(symbol, str(closes[-1]))

    created = await async_client.post(
        PORTFOLIOS,
        json={"name": "Risk", "base_currency": "USD", "initial_capital": "100000"},
        headers=auth_headers,
    )
    portfolio_id = created.json()["id"]
    await buy(async_client, auth_headers, portfolio_id, aapl, "200")
    await buy(async_client, auth_headers, portfolio_id, msft, "100")
    return portfolio_id, auth_headers, histories


async def test_portfolio_historical_report(async_client: AsyncClient, funded):
    portfolio_id, headers, histories = funded

    response = await async_client.post(
        BASE,
        json={"portfolio_id": portfolio_id, "lookback_days": 100, "confidence_level": 0.95},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    report = response.json()
    results = report["results"]

    assert report["method"] == "HISTORICAL"
    assert report["portfolio_id"] == portfolio_id and report["experiment_id"] is None
    assert report["configuration"] == {
        "method": "HISTORICAL",
        "confidence_level": 0.95,
        "horizon_days": 1,
        "lookback_days": 100,
        "random_seed": None,
    }
    assert results["subject"] == "PORTFOLIO"
    assert results["currency"] == "USD"
    assert results["observations"] == 100
    assert results["horizon_scaling"] == "sqrt_time"
    assert 0 < results["value_at_risk"] <= results["expected_shortfall"]
    assert set(results["data_fingerprints"]) == {p["instrument_id"] for p in results["positions"]}
    assert results["warnings"] == []

    # Weights describe the whole account: positions plus cash.
    weights = {p["symbol"]: p["weight"] for p in results["positions"]}
    assert set(weights) == {"AAPL", "MSFT"}
    assert sum(weights.values()) + results["cash_weight"] == pytest.approx(1.0, abs=1e-6)

    # Independent recomputation from the same price history.
    aapl = np.array(histories["AAPL"][-101:])
    msft = np.array(histories["MSFT"][-101:])
    cash = D((await async_client.get(f"{PORTFOLIOS}/{portfolio_id}", headers=headers)).json()[
        "cash_balance"
    ])
    value = float(cash) + 200 * aapl[-1] + 100 * msft[-1]
    portfolio_returns = (
        (200 * aapl[-1] / value) * (aapl[1:] / aapl[:-1] - 1)
        + (100 * msft[-1] / value) * (msft[1:] / msft[:-1] - 1)
    )
    expected_var = -np.quantile(portfolio_returns, 0.05)
    assert results["value_at_risk"] == pytest.approx(expected_var, rel=1e-6)
    assert float(results["portfolio_value"]) == pytest.approx(value, rel=1e-9)
    assert float(results["value_at_risk_amount"]) == pytest.approx(expected_var * value, rel=1e-6)
    assert weights["AAPL"] == pytest.approx(200 * aapl[-1] / value, rel=1e-6)


async def test_stale_cached_history_is_refreshed(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    """A report must describe today's risk, not the risk as of the last cache fill."""
    today = datetime.now(UTC).date()
    closes = random_walk(200, seed=9)
    fake_provider.history["AAPL"] = make_bars(closes[:150], end=today - timedelta(days=20))
    fake_provider.set_quote("AAPL", "100")
    created = await async_client.post(
        PORTFOLIOS,
        json={"name": "Stale", "base_currency": "USD", "initial_capital": "100000"},
        headers=auth_headers,
    )
    portfolio_id = created.json()["id"]
    await buy(async_client, auth_headers, portfolio_id, instruments["XNAS:AAPL"], "100")
    body = {"portfolio_id": portfolio_id, "lookback_days": 40}

    # The provider itself is behind: the report is produced but says so.
    behind = (await async_client.post(BASE, json=body, headers=auth_headers)).json()
    assert behind["results"]["as_of"] < (today - timedelta(days=15)).isoformat()
    assert len(behind["results"]["warnings"]) == 1
    assert behind["results"]["warnings"][0].startswith("AAPL: Data ends on")

    # The provider catches up. The stale cache must not be served again.
    fake_provider.history["AAPL"] = make_bars(closes, end=today)
    fresh = (await async_client.post(BASE, json=body, headers=auth_headers)).json()
    assert fresh["results"]["as_of"] >= (today - timedelta(days=2)).isoformat()
    assert fresh["results"]["warnings"] == []
    assert (
        fresh["results"]["data_fingerprints"] != behind["results"]["data_fingerprints"]
    )


async def test_methods_and_horizon(async_client: AsyncClient, funded):
    portfolio_id, headers, _ = funded

    async def report(**body) -> dict:
        response = await async_client.post(
            BASE, json={"portfolio_id": portfolio_id, "lookback_days": 100, **body}, headers=headers
        )
        assert response.status_code == 201, response.text
        return response.json()

    historical = await report(method="HISTORICAL")
    parametric = await report(method="PARAMETRIC")
    simulated = await report(method="MONTE_CARLO", random_seed=7, simulations=50000)

    # Three estimators of the same risk should land in the same neighbourhood.
    reference = parametric["results"]["value_at_risk"]
    assert historical["results"]["value_at_risk"] == pytest.approx(reference, rel=0.35)
    assert simulated["results"]["value_at_risk"] == pytest.approx(reference, rel=0.15)
    assert simulated["results"]["horizon_scaling"] == "simulated"
    assert simulated["configuration"]["simulations"] == 50000

    ten_day = await report(method="PARAMETRIC", horizon_days=10)
    assert ten_day["results"]["value_at_risk"] > 2 * reference
    stricter = await report(method="PARAMETRIC", confidence_level=0.99)
    assert stricter["results"]["value_at_risk"] > reference


async def test_monte_carlo_seed_is_recorded_and_reproducible(async_client: AsyncClient, funded):
    portfolio_id, headers, _ = funded
    body = {"portfolio_id": portfolio_id, "method": "MONTE_CARLO", "lookback_days": 100}

    seeded = [
        (await async_client.post(BASE, json={**body, "random_seed": 123}, headers=headers)).json()
        for _ in range(2)
    ]
    assert seeded[0]["results"]["value_at_risk"] == seeded[1]["results"]["value_at_risk"]
    assert seeded[0]["configuration"]["random_seed"] == 123

    # With no seed supplied one is generated and stored, so the run can be repeated.
    unseeded = (await async_client.post(BASE, json=body, headers=headers)).json()
    generated = unseeded["configuration"]["random_seed"]
    assert isinstance(generated, int)
    replay = (
        await async_client.post(BASE, json={**body, "random_seed": generated}, headers=headers)
    ).json()
    assert replay["results"]["value_at_risk"] == unseeded["results"]["value_at_risk"]


async def test_portfolio_report_requires_positions_and_history(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    created = await async_client.post(
        PORTFOLIOS,
        json={"name": "Empty", "base_currency": "USD", "initial_capital": "1000"},
        headers=auth_headers,
    )
    portfolio_id = created.json()["id"]
    empty = await async_client.post(BASE, json={"portfolio_id": portfolio_id}, headers=auth_headers)
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Portfolio has no positions to analyse"

    # A position with only twenty sessions of history cannot support an estimate.
    closes = random_walk(20, seed=3)
    fake_provider.history["AAPL"] = recent_bars(closes)
    fake_provider.set_quote("AAPL", "100")
    await buy(async_client, auth_headers, portfolio_id, instruments["XNAS:AAPL"], "1")
    thin = await async_client.post(BASE, json={"portfolio_id": portfolio_id}, headers=auth_headers)
    assert thin.status_code == 422
    assert "At least 30 return observations" in thin.json()["detail"]


async def test_provider_outage_surfaces_as_bad_gateway(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    fake_provider.set_quote("AAPL", "100")
    created = await async_client.post(
        PORTFOLIOS,
        json={"name": "Outage", "base_currency": "USD", "initial_capital": "1000"},
        headers=auth_headers,
    )
    portfolio_id = created.json()["id"]
    await buy(async_client, auth_headers, portfolio_id, instruments["XNAS:AAPL"], "1")

    fake_provider.error = ProviderConnectionError("upstream down", provider="YAHOO")
    response = await async_client.post(
        BASE, json={"portfolio_id": portfolio_id}, headers=auth_headers
    )
    assert response.status_code == 502
    assert "upstream down" in response.json()["detail"]
    assert (await async_client.get(BASE, headers=auth_headers)).json() == []


async def run_backtest(client, headers, instrument, fake_provider, bars: int = 200) -> dict:
    fake_provider.history[instrument.symbol] = make_bars(wave_closes(bars))
    strategy = await client.post(
        "/api/v1/strategies",
        json={"name": "BH", "strategy_type": "BUY_AND_HOLD"},
        headers=headers,
    )
    response = await client.post(
        "/api/v1/experiments/backtests",
        json={
            "strategy_version_id": strategy.json()["latest_version"]["id"],
            "instrument_id": str(instrument.id),
            "start": "2024-01-01",
            "end": "2024-12-31",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_experiment_report(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    experiment = await run_backtest(
        async_client, auth_headers, instruments["XNAS:AAPL"], fake_provider
    )
    response = await async_client.post(
        BASE,
        json={"experiment_id": experiment["id"], "method": "PARAMETRIC", "lookback_days": 120},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    report = response.json()
    results = report["results"]

    assert report["experiment_id"] == experiment["id"] and report["portfolio_id"] is None
    assert results["subject"] == "EXPERIMENT"
    assert results["observations"] == 120
    assert results["portfolio_value"] == experiment["results"]["final_equity"]
    assert results["data_fingerprint"] == experiment["data_fingerprint"]
    assert results["periods_per_year"] == 252
    assert results["value_at_risk"] > 0

    # Recompute from the stored equity curve.
    equity = np.array([float(p["equity"]) for p in experiment["results"]["equity_curve"]])
    returns = (equity[1:] / equity[:-1] - 1)[-120:]
    assert results["worst_period_return"] == pytest.approx(returns.min(), rel=1e-6)
    assert results["annualized_volatility"] == pytest.approx(
        returns.std(ddof=1) * np.sqrt(252), rel=1e-6
    )


async def test_failed_experiment_cannot_be_analysed(
    async_client: AsyncClient, auth_headers, instruments, fake_provider: FakeProvider
):
    experiment = await run_backtest(
        async_client, auth_headers, instruments["XNAS:AAPL"], fake_provider, bars=1
    )
    assert experiment["status"] == "FAILED"
    response = await async_client.post(
        BASE, json={"experiment_id": experiment["id"]}, headers=auth_headers
    )
    assert response.status_code == 422
    assert "no completed results" in response.json()["detail"]


async def test_request_validation(async_client: AsyncClient, auth_headers):
    subject = {"portfolio_id": str(uuid.uuid4())}
    for body in [
        {},
        {"portfolio_id": str(uuid.uuid4()), "experiment_id": str(uuid.uuid4())},
        {**subject, "confidence_level": 1.0},
        {**subject, "confidence_level": 0.5},
        {**subject, "horizon_days": 0},
        {**subject, "lookback_days": 10},
        {**subject, "method": "GUESS"},
        {**subject, "simulations": 10},
    ]:
        response = await async_client.post(BASE, json=body, headers=auth_headers)
        assert response.status_code == 422, body

    assert (await async_client.post(BASE, json=subject, headers=auth_headers)).status_code == 404
    assert (
        await async_client.post(
            BASE, json={"experiment_id": str(uuid.uuid4())}, headers=auth_headers
        )
    ).status_code == 404
    assert (await async_client.post(BASE, json=subject)).status_code == 401


async def test_reports_are_listed_filtered_and_private(
    async_client: AsyncClient, funded, make_user, instruments, fake_provider: FakeProvider
):
    portfolio_id, alice, _ = funded
    _, bob = await make_user()
    experiment = await run_backtest(async_client, alice, instruments["XNYS:SPY"], fake_provider)

    portfolio_report = (
        await async_client.post(
            BASE, json={"portfolio_id": portfolio_id, "lookback_days": 100}, headers=alice
        )
    ).json()
    experiment_report = (
        await async_client.post(BASE, json={"experiment_id": experiment["id"]}, headers=alice)
    ).json()

    everything = (await async_client.get(BASE, headers=alice)).json()
    assert {r["id"] for r in everything} == {portfolio_report["id"], experiment_report["id"]}
    by_portfolio = (
        await async_client.get(f"{BASE}?portfolio_id={portfolio_id}", headers=alice)
    ).json()
    assert [r["id"] for r in by_portfolio] == [portfolio_report["id"]]
    by_experiment = (
        await async_client.get(f"{BASE}?experiment_id={experiment['id']}", headers=alice)
    ).json()
    assert [r["id"] for r in by_experiment] == [experiment_report["id"]]

    fetched = await async_client.get(f"{BASE}/{portfolio_report['id']}", headers=alice)
    assert fetched.json() == portfolio_report

    # Bob can neither read Alice's reports nor create reports about her resources.
    assert (await async_client.get(BASE, headers=bob)).json() == []
    assert (
        await async_client.get(f"{BASE}/{portfolio_report['id']}", headers=bob)
    ).status_code == 404
    assert (
        await async_client.post(BASE, json={"portfolio_id": portfolio_id}, headers=bob)
    ).status_code == 404
    assert (
        await async_client.post(BASE, json={"experiment_id": experiment["id"]}, headers=bob)
    ).status_code == 404

    # Deleting the experiment keeps its report, unlinked.
    await async_client.delete(f"/api/v1/experiments/{experiment['id']}", headers=alice)
    orphan = (await async_client.get(f"{BASE}/{experiment_report['id']}", headers=alice)).json()
    assert orphan["experiment_id"] is None
    assert orphan["results"] == experiment_report["results"]
