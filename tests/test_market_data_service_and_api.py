"""Tests for Market Data API endpoints and MarketDataService."""

import uuid
from unittest.mock import patch

import pandas as pd
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_data.seed import seed_market_reference_data
from app.models.identity import Instrument


@pytest.fixture
async def seeded_market(db_session: AsyncSession):
    await seed_market_reference_data(db_session)
    await db_session.flush()


@pytest.fixture
def market_client(async_client: AsyncClient, auth_headers: dict[str, str]) -> AsyncClient:
    """The shared test client, signed in: market-data endpoints require a user."""
    async_client.headers.update(auth_headers)
    return async_client


PROTECTED_PATHS = [
    "/api/v1/market-data/instruments",
    "/api/v1/market-data/providers",
    f"/api/v1/market-data/{uuid.uuid4()}/historical",
    # Auth is checked before the query string, so a bad request reveals nothing either.
    f"/api/v1/market-data/{uuid.uuid4()}/historical?interval=999x&limit=0",
]


@pytest.mark.parametrize("path", PROTECTED_PATHS)
async def test_market_data_requires_authentication(async_client: AsyncClient, path: str):
    response = await async_client.get(path)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("path", PROTECTED_PATHS)
async def test_market_data_rejects_invalid_token(async_client: AsyncClient, path: str):
    response = await async_client.get(path, headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_market_data_rejects_inactive_user(
    async_client: AsyncClient, db_session: AsyncSession, make_user
):
    user, headers = await make_user()
    user.is_active = False
    await db_session.flush()

    response = await async_client.get("/api/v1/market-data/providers", headers=headers)
    assert response.status_code == 403


async def test_anonymous_request_never_reaches_the_provider(
    async_client: AsyncClient, db_session: AsyncSession, seeded_market
):
    aapl_id = (
        await db_session.execute(select(Instrument.id).where(Instrument.symbol == "AAPL"))
    ).scalar_one()

    with patch("yfinance.download") as download:
        response = await async_client.get(
            f"/api/v1/market-data/{aapl_id}/historical?force_refresh=true"
        )

    assert response.status_code == 401
    download.assert_not_called()


@pytest.mark.asyncio
async def test_api_list_market_data_instruments(market_client: AsyncClient, seeded_market):
    # 1. List all instruments
    response = await market_client.get("/api/v1/market-data/instruments")
    assert response.status_code == 200
    instruments = response.json()
    assert len(instruments) >= 6

    # 2. Filter by exchange MIC (XNSE)
    nse_response = await market_client.get("/api/v1/market-data/instruments?exchange_mic=XNSE")
    assert nse_response.status_code == 200
    nse_instruments = nse_response.json()
    assert len(nse_instruments) >= 3
    for inst in nse_instruments:
        assert inst["exchange"]["mic"] == "XNSE"
        assert inst["currency_code"] == "INR"

    # 3. Filter by exchange MIC (XNAS)
    nasdaq_response = await market_client.get("/api/v1/market-data/instruments?exchange_mic=XNAS")
    assert nasdaq_response.status_code == 200
    nasdaq_instruments = nasdaq_response.json()
    assert len(nasdaq_instruments) >= 2
    for inst in nasdaq_instruments:
        assert inst["exchange"]["mic"] == "XNAS"
        assert inst["currency_code"] == "USD"

    # 4. Search query
    search_response = await market_client.get("/api/v1/market-data/instruments?search=Apple")
    assert search_response.status_code == 200
    search_data = search_response.json()
    assert len(search_data) == 1
    assert search_data[0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_api_list_market_data_providers(market_client: AsyncClient):
    response = await market_client.get("/api/v1/market-data/providers")
    assert response.status_code == 200
    providers = response.json()
    assert len(providers) >= 1
    yahoo = next(p for p in providers if p["name"] == "YAHOO")
    assert "1d" in yahoo["supported_intervals"]
    assert yahoo["is_realtime"] is False
    assert yahoo["is_exchange_grade"] is False


@pytest.mark.asyncio
async def test_api_historical_data_mocked_fetch_and_fingerprint(
    market_client: AsyncClient, db_session: AsyncSession, seeded_market
):
    # Fetch AAPL instrument
    inst_resp = await market_client.get("/api/v1/market-data/instruments?search=AAPL")
    aapl_id = inst_resp.json()[0]["id"]

    # Mock yfinance return
    idx = pd.date_range("2026-11-02", periods=3, freq="D", tz="UTC")
    mock_df = pd.DataFrame(
        {
            "Open": [150.0, 152.0, 151.0],
            "High": [155.0, 156.0, 154.0],
            "Low": [149.0, 150.5, 150.0],
            "Close": [153.0, 151.5, 153.5],
            "Volume": [1000000, 1200000, 1100000],
        },
        index=idx,
    )

    with patch("yfinance.download", return_value=mock_df):
        resp = await market_client.get(
            f"/api/v1/market-data/{aapl_id}/historical?interval=1d&adjusted=true"
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["instrument_id"] == aapl_id
        assert data["symbol"] == "AAPL"
        assert data["exchange_mic"] == "XNAS"
        assert data["currency"] == "USD"
        assert data["count"] == 3
        assert len(data["candles"]) == 3
        assert data["data_fingerprint"] is not None
        assert len(data["data_fingerprint"]) == 64

    # Verify data was persisted in database by querying again WITHOUT mock
    cached_resp = await market_client.get(
        f"/api/v1/market-data/{aapl_id}/historical?interval=1d&adjusted=true"
    )
    assert cached_resp.status_code == 200
    cached_data = cached_resp.json()
    assert cached_data["count"] == 3
    assert cached_data["data_fingerprint"] == data["data_fingerprint"]


@pytest.mark.asyncio
async def test_api_historical_data_date_range_validation_failure(
    market_client: AsyncClient, seeded_market
):
    inst_resp = await market_client.get("/api/v1/market-data/instruments?search=AAPL")
    aapl_id = inst_resp.json()[0]["id"]

    # start after end should be rejected with 422
    resp = await market_client.get(
        f"/api/v1/market-data/{aapl_id}/historical?start=2026-11-10T00:00:00Z&end=2026-11-01T00:00:00Z"
    )
    assert resp.status_code == 422
    assert "Start date" in str(resp.json())


@pytest.mark.asyncio
async def test_api_historical_data_invalid_interval(
    market_client: AsyncClient, seeded_market
):
    inst_resp = await market_client.get("/api/v1/market-data/instruments?search=AAPL")
    aapl_id = inst_resp.json()[0]["id"]

    resp = await market_client.get(
        f"/api/v1/market-data/{aapl_id}/historical?interval=999x"
    )
    assert resp.status_code == 400
    assert "not supported" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_api_historical_data_instrument_not_found(market_client: AsyncClient):
    random_id = uuid.uuid4()
    resp = await market_client.get(f"/api/v1/market-data/{random_id}/historical")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_api_historical_data_provider_rate_limit(
    market_client: AsyncClient, seeded_market
):
    inst_resp = await market_client.get("/api/v1/market-data/instruments?search=AAPL")
    aapl_id = inst_resp.json()[0]["id"]

    with patch("yfinance.download", side_effect=Exception("HTTP 429 Too Many Requests")):
        resp = await market_client.get(
            f"/api/v1/market-data/{aapl_id}/historical?force_refresh=true"
        )
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_api_historical_data_provider_connection_error(
    market_client: AsyncClient, seeded_market
):
    inst_resp = await market_client.get("/api/v1/market-data/instruments?search=AAPL")
    aapl_id = inst_resp.json()[0]["id"]

    with patch("yfinance.download", side_effect=ConnectionError("DNS failure")):
        resp = await market_client.get(
            f"/api/v1/market-data/{aapl_id}/historical?force_refresh=true"
        )
        assert resp.status_code == 502
        assert "Upstream provider failure" in resp.json()["detail"]
