"""The synthetic (made-up, repeatable) market data provider."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from test_config import build
from test_experiments_api import make_strategy_version

from app.api.deps import get_market_data_provider
from app.main import app
from app.market_data.exceptions import NoDataFoundError
from app.market_data.synthetic import SyntheticProvider
from app.market_data.types import PriceAdjustmentMode

START = datetime(2023, 1, 1, tzinfo=UTC)
END = datetime(2023, 12, 31, tzinfo=UTC)


@pytest.fixture
def synthetic(async_client: AsyncClient) -> SyntheticProvider:
    provider = SyntheticProvider()
    app.dependency_overrides[get_market_data_provider] = lambda: provider
    return provider


async def test_bars_are_valid_weekday_bars():
    bars = await SyntheticProvider().fetch_historical_bars("XNAS:AAPL", START, END)
    assert 255 <= len(bars) <= 262
    assert all(bar.timestamp.weekday() < 5 for bar in bars)
    assert [bar.timestamp for bar in bars] == sorted({bar.timestamp for bar in bars})
    for bar in bars:
        assert 0 < bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
        assert bar.volume > 0
        assert bar.provider == "SYNTHETIC"
        assert bar.timestamp.tzinfo is not None


async def test_the_same_symbol_always_has_the_same_history():
    first = await SyntheticProvider().fetch_historical_bars("XNAS:AAPL", START, END)
    again = await SyntheticProvider().fetch_historical_bars("XNAS:AAPL", START, END)
    assert [(b.timestamp, b.open, b.close) for b in first] == [
        (b.timestamp, b.open, b.close) for b in again
    ]


async def test_a_bar_does_not_depend_on_the_range_requested():
    provider = SyntheticProvider()
    year = await provider.fetch_historical_bars("XNAS:AAPL", START, END)
    quarter = await provider.fetch_historical_bars(
        "XNAS:AAPL", datetime(2023, 4, 1, tzinfo=UTC), datetime(2023, 6, 30, tzinfo=UTC)
    )
    by_day = {bar.timestamp: bar for bar in year}
    assert quarter and all(by_day[bar.timestamp].close == bar.close for bar in quarter)


async def test_symbols_differ_from_each_other():
    provider = SyntheticProvider()
    aapl = await provider.fetch_historical_bars("XNAS:AAPL", START, END)
    msft = await provider.fetch_historical_bars("XNAS:MSFT", START, END)
    assert [bar.close for bar in aapl] != [bar.close for bar in msft]
    assert provider.resolve_symbol("XNAS", "TCS") != provider.resolve_symbol("XBOM", "TCS")


@pytest.mark.parametrize(("interval", "expected"), [("1wk", (50, 54)), ("1mo", (12, 12))])
async def test_weekly_and_monthly_bars_aggregate_the_daily_ones(interval, expected):
    provider = SyntheticProvider()
    daily = await provider.fetch_historical_bars("XNAS:AAPL", START, END)
    bars = await provider.fetch_historical_bars("XNAS:AAPL", START, END, interval=interval)
    assert expected[0] <= len(bars) <= expected[1]
    assert max(bar.high for bar in bars) == max(bar.high for bar in daily)
    assert sum(bar.volume for bar in bars) <= sum(bar.volume for bar in daily)
    assert bars[-1].close == daily[-1].close
    assert all(bar.low <= bar.close <= bar.high for bar in bars)


async def test_there_is_a_bar_recent_enough_to_quote_against():
    now = datetime.now(UTC)
    bars = await SyntheticProvider().fetch_historical_bars(
        "XNAS:AAPL", now - timedelta(days=10), now, adjustment_mode=PriceAdjustmentMode.RAW
    )
    assert now - bars[-1].timestamp < timedelta(days=4)  # at worst, Friday seen on Monday


async def test_no_data_before_the_epoch_or_for_unsupported_intervals():
    provider = SyntheticProvider()
    with pytest.raises(NoDataFoundError):
        await provider.fetch_historical_bars(
            "XNAS:AAPL", datetime(2001, 1, 1, tzinfo=UTC), datetime(2001, 6, 1, tzinfo=UTC)
        )
    with pytest.raises(NoDataFoundError):
        await provider.fetch_historical_bars("XNAS:AAPL", START, END, interval="5m")


async def test_the_whole_product_works_on_synthetic_prices(
    async_client: AsyncClient, auth_headers, instruments, synthetic
):
    """Passes the same validation, storage and execution paths as real data."""
    aapl = str(instruments["XNAS:AAPL"].id)
    providers = await async_client.get("/api/v1/market-data/providers", headers=auth_headers)
    assert [p["name"] for p in providers.json()] == ["SYNTHETIC"]

    history = await async_client.get(
        f"/api/v1/market-data/{aapl}/historical?start=2023-01-01T00:00:00Z&end=2023-12-31T00:00:00Z",
        headers=auth_headers,
    )
    assert history.status_code == 200, history.text
    assert history.json()["count"] > 250

    version_id = await make_strategy_version(async_client, auth_headers)
    backtest = await async_client.post(
        "/api/v1/experiments/backtests",
        json={
            "strategy_version_id": version_id,
            "instrument_id": aapl,
            "start": "2021-01-01",
            "end": "2023-12-31",
        },
        headers=auth_headers,
    )
    assert backtest.status_code == 201, backtest.text
    body = backtest.json()
    assert body["status"] == "COMPLETED", body["error_message"]
    assert body["results"]["data"]["provider"] == "SYNTHETIC"
    assert body["results"]["metrics"]["closed_trades"] > 0

    portfolio = (
        await async_client.post(
            "/api/v1/portfolios",
            json={"name": "P", "base_currency": "USD", "initial_capital": "100000"},
            headers=auth_headers,
        )
    ).json()
    order = await async_client.post(
        f"/api/v1/portfolios/{portfolio['id']}/orders",
        json={"instrument_id": aapl, "side": "BUY", "quantity": "5"},
        headers=auth_headers,
    )
    assert order.json()["status"] == "FILLED", order.text

    risk = await async_client.post(
        "/api/v1/risk/reports", json={"portfolio_id": portfolio["id"]}, headers=auth_headers
    )
    assert risk.status_code == 201, risk.text
    assert 0 < risk.json()["results"]["value_at_risk"] < 0.2


async def test_requests_use_the_configured_provider_unless_one_is_named(
    async_client: AsyncClient, auth_headers, instruments, synthetic
):
    aapl = str(instruments["XNAS:AAPL"].id)
    named = await async_client.get(
        f"/api/v1/market-data/{aapl}/historical?provider=YAHOO", headers=auth_headers
    )
    assert named.status_code == 400
    assert "not registered" in named.json()["detail"]


def test_synthetic_prices_are_refused_in_production():
    assert build(app_env="development", market_data_provider="Synthetic").market_data_provider == (
        "synthetic"
    )
    with pytest.raises(ValidationError, match="made-up prices"):
        build(market_data_provider="synthetic")
    with pytest.raises(ValidationError, match="MARKET_DATA_PROVIDER"):
        build(market_data_provider="bloomberg")
