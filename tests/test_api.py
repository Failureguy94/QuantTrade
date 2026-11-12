import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.identity import Exchange, Instrument


@pytest.mark.asyncio
async def test_app_startup_and_root(async_client: AsyncClient):
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == settings.app_name
    assert data["version"] == "0.1.0"
    assert data["docs"] == "/docs"


@pytest.mark.asyncio
async def test_health_endpoints(async_client: AsyncClient):
    # GET /api/v1/health
    res = await async_client.get(f"{settings.api_v1_prefix}/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "quant-api"}

    # GET /api/v1/health/database
    res_db = await async_client.get(f"{settings.api_v1_prefix}/health/database")
    assert res_db.status_code == 200
    assert res_db.json() == {"status": "ok", "database": "connected"}


@pytest.mark.asyncio
async def test_cors_preflight_and_headers(async_client: AsyncClient):
    # Test OPTIONS preflight request
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization,Content-Type",
    }
    response = await async_client.options(f"{settings.api_v1_prefix}/health", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "POST" in response.headers.get("access-control-allow-methods", "")

    # Test Disallowed origin
    bad_headers = {
        "Origin": "http://unauthorized-evil-site.com",
        "Access-Control-Request-Method": "GET",
    }
    bad_response = await async_client.options(
        f"{settings.api_v1_prefix}/health", headers=bad_headers
    )
    assert bad_response.headers.get("access-control-allow-origin") is None


@pytest.mark.asyncio
async def test_instruments_endpoint_listing(async_client: AsyncClient, db_session: AsyncSession):
    exchange = Exchange(
        mic="XPAR",
        name="Euronext Paris",
        country_code="FR",
        timezone="Europe/Paris",
        currency_code="EUR",
        calendar_code="XPAR",
    )
    db_session.add(exchange)
    await db_session.flush()

    inst = Instrument(
        exchange_id=exchange.id,
        symbol="MC",
        name="LVMH Moet Hennessy",
        currency_code="EUR",
        asset_type="EQUITY",
    )
    db_session.add(inst)
    await db_session.flush()

    # List all
    response = await async_client.get(f"{settings.api_v1_prefix}/instruments")
    assert response.status_code == 200
    items = response.json()
    assert len(items) >= 1
    assert any(i["symbol"] == "MC" for i in items)

    # Filter by exchange_id
    url = f"{settings.api_v1_prefix}/instruments?exchange_id={exchange.id}"
    res_filtered = await async_client.get(url)
    assert res_filtered.status_code == 200
    filtered_items = res_filtered.json()
    assert len(filtered_items) == 1
    assert filtered_items[0]["symbol"] == "MC"


@pytest.mark.asyncio
async def test_validation_error_responses(async_client: AsyncClient):
    # Invalid limit < 1
    res1 = await async_client.get(f"{settings.api_v1_prefix}/instruments?limit=0")
    assert res1.status_code == 422
    assert "detail" in res1.json()

    # Invalid limit > 500
    res2 = await async_client.get(f"{settings.api_v1_prefix}/instruments?limit=999")
    assert res2.status_code == 422
    assert "detail" in res2.json()

    # Invalid UUID format for exchange_id
    bad_url = f"{settings.api_v1_prefix}/instruments?exchange_id=invalid-uuid-format"
    res3 = await async_client.get(bad_url)
    assert res3.status_code == 422
    err_detail = res3.json()["detail"]
    assert any(e["loc"][-1] == "exchange_id" for e in err_detail)


@pytest.mark.asyncio
async def test_not_found_endpoint(async_client: AsyncClient):
    res = await async_client.get("/api/v1/non_existent_route")
    assert res.status_code == 404
    assert res.json() == {"detail": "Not Found"}
