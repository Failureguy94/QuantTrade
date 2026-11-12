"""API tests for the strategy catalogue, strategies and versions."""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting.engine import ENGINE_VERSION
from app.models.research import StrategyVersion

BASE = "/api/v1/strategies"


async def create_strategy(client: AsyncClient, headers: dict, **overrides) -> dict:
    payload = {
        "name": "Trend follower",
        "strategy_type": "SMA_CROSSOVER",
        "parameters": {"fast_window": 5, "slow_window": 20},
        **overrides,
    }
    response = await client.post(BASE, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_endpoints_require_authentication(async_client: AsyncClient):
    strategy_id = uuid.uuid4()
    for method, url in [
        ("GET", f"{BASE}/types"),
        ("GET", BASE),
        ("POST", BASE),
        ("GET", f"{BASE}/{strategy_id}"),
        ("PATCH", f"{BASE}/{strategy_id}"),
        ("GET", f"{BASE}/{strategy_id}/versions"),
        ("POST", f"{BASE}/{strategy_id}/versions"),
    ]:
        response = await async_client.request(method, url, json={})
        assert response.status_code == 401, f"{method} {url}"


async def test_strategy_type_catalogue(async_client: AsyncClient, auth_headers):
    response = await async_client.get(f"{BASE}/types", headers=auth_headers)
    assert response.status_code == 200
    types = {item["type"]: item for item in response.json()}
    assert set(types) == {"BUY_AND_HOLD", "SMA_CROSSOVER", "MOMENTUM", "MEAN_REVERSION"}
    sma = types["SMA_CROSSOVER"]
    assert sma["default_parameters"] == {"fast_window": 20, "slow_window": 50}
    assert set(sma["parameter_schema"]["properties"]) == {"fast_window", "slow_window"}


async def test_create_strategy_creates_version_one(
    async_client: AsyncClient, auth_headers, db_session: AsyncSession
):
    created = await create_strategy(
        async_client,
        auth_headers,
        name="  Trend follower  ",
        strategy_type="sma_crossover",
        description="Daily crossover",
        parameters={"fast_window": 10},
    )
    assert created["name"] == "Trend follower"
    assert created["strategy_type"] == "SMA_CROSSOVER"
    assert created["is_archived"] is False
    latest = created["latest_version"]
    assert latest["version"] == 1
    # Defaults are filled in so the stored version is fully specified.
    assert latest["parameters"] == {"fast_window": 10, "slow_window": 50}
    assert latest["code_version"] == ENGINE_VERSION

    stored = (
        await db_session.execute(
            select(StrategyVersion).where(StrategyVersion.strategy_id == uuid.UUID(created["id"]))
        )
    ).scalars().all()
    assert len(stored) == 1


async def test_create_strategy_validation(async_client: AsyncClient, auth_headers):
    cases = [
        ({"strategy_type": "STAT_ARB"}, "Unknown strategy type"),
        ({"parameters": {"fast_window": 50, "slow_window": 10}}, "fast_window must be smaller"),
        ({"parameters": {"unknown": 1}}, "unknown"),
    ]
    for overrides, fragment in cases:
        payload = {"name": "X", "strategy_type": "SMA_CROSSOVER", "parameters": {}, **overrides}
        response = await async_client.post(BASE, json=payload, headers=auth_headers)
        assert response.status_code == 422, response.text
        assert fragment in response.json()["detail"]

    blank = await async_client.post(
        BASE, json={"name": "   ", "strategy_type": "MOMENTUM"}, headers=auth_headers
    )
    assert blank.status_code == 422


async def test_versions_are_appended_and_immutable(async_client: AsyncClient, auth_headers):
    strategy = await create_strategy(async_client, auth_headers)
    url = f"{BASE}/{strategy['id']}/versions"

    second = await async_client.post(
        url, json={"parameters": {"fast_window": 8, "slow_window": 30}}, headers=auth_headers
    )
    assert second.status_code == 201
    assert second.json()["version"] == 2
    third = await async_client.post(url, json={"parameters": {}}, headers=auth_headers)
    assert third.json()["version"] == 3
    assert third.json()["parameters"] == {"fast_window": 20, "slow_window": 50}

    invalid = await async_client.post(
        url, json={"parameters": {"slow_window": 1}}, headers=auth_headers
    )
    assert invalid.status_code == 422

    versions = (await async_client.get(url, headers=auth_headers)).json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    # Version 1 is untouched by later versions.
    assert versions[-1]["parameters"] == {"fast_window": 5, "slow_window": 20}

    detail = (await async_client.get(f"{BASE}/{strategy['id']}", headers=auth_headers)).json()
    assert detail["latest_version"]["version"] == 3


async def test_update_and_archive(async_client: AsyncClient, auth_headers):
    strategy = await create_strategy(async_client, auth_headers, description="old")
    url = f"{BASE}/{strategy['id']}"

    renamed = await async_client.patch(url, json={"name": "Renamed"}, headers=auth_headers)
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    assert renamed.json()["description"] == "old"  # untouched fields are preserved

    cleared = await async_client.patch(url, json={"description": None}, headers=auth_headers)
    assert cleared.json()["description"] is None

    archived = await async_client.patch(url, json={"is_archived": True}, headers=auth_headers)
    assert archived.json()["is_archived"] is True

    visible = (await async_client.get(BASE, headers=auth_headers)).json()
    assert visible == []
    everything = (
        await async_client.get(f"{BASE}?include_archived=true", headers=auth_headers)
    ).json()
    assert [s["id"] for s in everything] == [strategy["id"]]


async def test_list_is_paginated_and_carries_latest_versions(
    async_client: AsyncClient, auth_headers
):
    ids = []
    for index in range(3):
        created = await create_strategy(async_client, auth_headers, name=f"S{index}")
        ids.append(created["id"])
    await async_client.post(
        f"{BASE}/{ids[0]}/versions", json={"parameters": {}}, headers=auth_headers
    )

    listed = (await async_client.get(BASE, headers=auth_headers)).json()
    assert {s["id"] for s in listed} == set(ids)
    by_id = {s["id"]: s for s in listed}
    assert by_id[ids[0]]["latest_version"]["version"] == 2
    assert by_id[ids[1]]["latest_version"]["version"] == 1

    page = (await async_client.get(f"{BASE}?limit=2&offset=2", headers=auth_headers)).json()
    assert len(page) == 1


async def test_strategies_are_private_to_their_owner(async_client: AsyncClient, make_user):
    _, alice = await make_user()
    _, bob = await make_user()
    strategy = await create_strategy(async_client, alice)
    url = f"{BASE}/{strategy['id']}"

    assert (await async_client.get(BASE, headers=bob)).json() == []
    # Another user's strategy is indistinguishable from a missing one.
    assert (await async_client.get(url, headers=bob)).status_code == 404
    assert (await async_client.patch(url, json={"name": "x"}, headers=bob)).status_code == 404
    assert (await async_client.get(f"{url}/versions", headers=bob)).status_code == 404
    assert (
        await async_client.post(f"{url}/versions", json={"parameters": {}}, headers=bob)
    ).status_code == 404

    assert (await async_client.get(url, headers=alice)).json()["name"] == "Trend follower"
    assert (await async_client.get(f"{BASE}/{uuid.uuid4()}", headers=alice)).status_code == 404
