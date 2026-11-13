"""The background worker: queued backtests, abandoned jobs and the order matcher."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_experiments_api import backtest_payload, make_strategy_version
from test_paper_trading_api import open_portfolio, place

from app.core.config import settings
from app.market_data.exceptions import ProviderConnectionError
from app.models.identity import User
from app.models.research import SimulationJob
from app.worker import Worker
from helpers import FakeProvider, make_bars, wave_closes

EXPERIMENTS = "/api/v1/experiments"
PORTFOLIOS = "/api/v1/portfolios"


async def enqueue(client: AsyncClient, headers, version_id: str, instrument_id, **overrides):
    return await client.post(
        f"{EXPERIMENTS}/backtests?async=true",
        json=backtest_payload(version_id, instrument_id, **overrides),
        headers=headers,
    )


async def fetch(client: AsyncClient, headers, experiment_id: str) -> dict:
    response = await client.get(f"{EXPERIMENTS}/{experiment_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------ queued backtests
async def test_async_backtest_is_queued_then_run_by_the_worker(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers)

    queued = await enqueue(async_client, auth_headers, version_id, aapl.id)
    assert queued.status_code == 202, queued.text
    experiment = queued.json()
    assert experiment["status"] == "QUEUED"
    assert experiment["results"] is None
    assert experiment["started_at"] is None
    assert fake_provider.calls == 0  # nothing ran inside the request

    jobs = (
        await async_client.get(f"{EXPERIMENTS}/{experiment['id']}/jobs", headers=auth_headers)
    ).json()
    assert [(job["status"], job["progress"]) for job in jobs] == [("QUEUED", 0)]

    assert await worker.run_pending_jobs() == 1
    assert await worker.run_pending_jobs() == 0  # nothing left

    done = await fetch(async_client, auth_headers, experiment["id"])
    assert done["status"] == "COMPLETED"
    assert done["started_at"] and done["completed_at"]
    assert done["results"]["metrics"]["closed_trades"] > 0
    jobs = (
        await async_client.get(f"{EXPERIMENTS}/{experiment['id']}/jobs", headers=auth_headers)
    ).json()
    assert [(job["status"], job["progress"]) for job in jobs] == [("COMPLETED", 100)]


async def test_a_queued_run_gives_the_same_result_as_an_immediate_one(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers)
    options = {"allow_short": True, "commission_bps": "7", "position_size": "0.5"}

    immediate = await async_client.post(
        f"{EXPERIMENTS}/backtests",
        json=backtest_payload(version_id, aapl.id, **options),
        headers=auth_headers,
    )
    assert immediate.status_code == 201
    queued = await enqueue(async_client, auth_headers, version_id, aapl.id, **options)
    await worker.run_pending_jobs()
    later = await fetch(async_client, auth_headers, queued.json()["id"])

    now = immediate.json()
    assert later["data_fingerprint"] == now["data_fingerprint"]
    assert later["results"]["final_equity"] == now["results"]["final_equity"]
    assert later["results"]["metrics"] == now["results"]["metrics"]
    assert later["configuration"] == now["configuration"]


async def test_a_run_that_fails_is_recorded_as_failed(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
):
    version_id = await make_strategy_version(async_client, auth_headers)
    queued = await enqueue(async_client, auth_headers, version_id, instruments["XNAS:AAPL"].id)
    assert queued.status_code == 202  # no data, but that is only known when it runs

    await worker.run_pending_jobs()
    failed = await fetch(async_client, auth_headers, queued.json()["id"])
    assert failed["status"] == "FAILED"
    assert failed["error_message"]
    assert failed["results"] is None


async def test_an_invalid_request_is_refused_and_nothing_is_queued(
    async_client: AsyncClient, auth_headers, instruments, db_session: AsyncSession
):
    response = await enqueue(
        async_client, auth_headers, str(uuid.uuid4()), instruments["XNAS:AAPL"].id
    )
    assert response.status_code == 404
    assert (await db_session.execute(select(SimulationJob))).scalars().all() == []


async def test_queue_is_capped_per_user(
    async_client: AsyncClient,
    make_user,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
    monkeypatch,
):
    monkeypatch.setattr(settings, "max_queued_runs_per_user", 2)
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    _, alice = await make_user()
    _, bob = await make_user()
    version_id = await make_strategy_version(async_client, alice)

    for _ in range(2):
        assert (await enqueue(async_client, alice, version_id, aapl.id)).status_code == 202
    refused = await enqueue(async_client, alice, version_id, aapl.id)
    assert refused.status_code == 429
    assert refused.json()["code"] == "rate_limited"
    assert "Retry-After" in refused.headers

    # Someone else's queue is their own, and finished runs free the slots.
    bob_version = await make_strategy_version(async_client, bob)
    assert (await enqueue(async_client, bob, bob_version, aapl.id)).status_code == 202
    assert await worker.run_pending_jobs() == 3
    assert (await enqueue(async_client, alice, version_id, aapl.id)).status_code == 202


async def test_jobs_run_oldest_first_and_respect_the_batch_limit(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
    db_session: AsyncSession,
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers)
    ids = []
    for index in range(3):
        response = await enqueue(async_client, auth_headers, version_id, aapl.id)
        ids.append(response.json()["id"])
        # created_at comes from the transaction clock; space the jobs out explicitly.
        await db_session.execute(
            update(SimulationJob)
            .where(SimulationJob.experiment_id == uuid.UUID(ids[-1]))
            .values(created_at=datetime.now(UTC) + timedelta(seconds=index))
        )

    assert await worker.run_pending_jobs(limit=2) == 2
    statuses = [(await fetch(async_client, auth_headers, i))["status"] for i in ids]
    assert statuses == ["COMPLETED", "COMPLETED", "QUEUED"]


async def test_a_job_whose_experiment_was_deleted_is_failed_not_run(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
    db_session: AsyncSession,
):
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers)
    queued = await enqueue(async_client, auth_headers, version_id, instruments["XNAS:AAPL"].id)
    deleted = await async_client.delete(
        f"{EXPERIMENTS}/{queued.json()['id']}", headers=auth_headers
    )
    assert deleted.status_code == 204

    assert await worker.run_pending_jobs() == 1
    db_session.expire_all()
    job = (await db_session.execute(select(SimulationJob))).scalar_one()
    assert job.status == "FAILED"
    assert "deleted" in job.error_message
    assert fake_provider.calls == 0


async def test_an_unexpected_crash_fails_the_job_and_the_worker_carries_on(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
):
    aapl = instruments["XNAS:AAPL"]
    version_id = await make_strategy_version(async_client, auth_headers)
    broken = await enqueue(async_client, auth_headers, version_id, aapl.id)
    fake_provider.error = RuntimeError("something nobody anticipated")
    assert await worker.run_pending_jobs() == 1

    crashed = await fetch(async_client, auth_headers, broken.json()["id"])
    assert crashed["status"] == "FAILED"
    assert crashed["error_message"] == "The run failed because of an internal error."
    assert "nobody anticipated" not in crashed["error_message"]  # internals stay in the log

    fake_provider.error = None
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    healthy = await enqueue(async_client, auth_headers, version_id, aapl.id)
    assert await worker.run_pending_jobs() == 1
    assert (await fetch(async_client, auth_headers, healthy.json()["id"]))["status"] == "COMPLETED"


async def test_abandoned_running_jobs_are_failed(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
    db_session: AsyncSession,
):
    aapl = instruments["XNAS:AAPL"]
    version_id = await make_strategy_version(async_client, auth_headers)
    stale = (await enqueue(async_client, auth_headers, version_id, aapl.id)).json()["id"]
    fresh = (await enqueue(async_client, auth_headers, version_id, aapl.id)).json()["id"]
    long_ago = datetime.now(UTC) - timedelta(minutes=settings.job_timeout_minutes + 1)
    await db_session.execute(
        update(SimulationJob)
        .where(SimulationJob.experiment_id == uuid.UUID(stale))
        .values(status="RUNNING", updated_at=long_ago)
    )
    await db_session.execute(
        update(SimulationJob)
        .where(SimulationJob.experiment_id == uuid.UUID(fresh))
        .values(status="RUNNING", updated_at=datetime.now(UTC))
    )

    assert await worker.fail_abandoned_jobs() == 1

    abandoned = await fetch(async_client, auth_headers, stale)
    assert abandoned["status"] == "FAILED"
    assert "interrupted" in abandoned["error_message"]
    db_session.expire_all()
    still_running = (
        await db_session.execute(
            select(SimulationJob).where(SimulationJob.experiment_id == uuid.UUID(fresh))
        )
    ).scalar_one()
    assert still_running.status == "RUNNING"


async def test_two_workers_never_claim_the_same_job(test_engine, fake_provider: FakeProvider):
    """Uses real, separately committed connections: row locks do not show otherwise."""
    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    user_id = uuid.uuid4()
    async with factory() as session:
        session.add(User(id=user_id, email=f"{user_id.hex}@quant.example", password_hash="x"))
        await session.flush()
        session.add(SimulationJob(user_id=user_id, job_type="BACKTEST", status="QUEUED"))
        await session.commit()
    try:
        first, second = Worker(factory, fake_provider), Worker(factory, fake_provider)
        # A third party holds the row lock mid-claim: a worker skips it instead of
        # waiting, which is what lets workers run side by side.
        async with factory() as holder:
            job_id = (
                await holder.execute(
                    select(SimulationJob.id)
                    .where(SimulationJob.user_id == user_id)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one()
            assert await asyncio.wait_for(first.claim_job(), timeout=5) is None
            await holder.rollback()

        claimed = await asyncio.gather(first.claim_job(), second.claim_job())
        assert sorted(claimed, key=lambda value: value is None) == [job_id, None]
    finally:
        async with factory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()


async def test_run_forever_processes_jobs_until_stopped(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
    monkeypatch,
):
    monkeypatch.setattr(settings, "worker_poll_seconds", 0.01)
    fake_provider.history["AAPL"] = make_bars(wave_closes(200))
    version_id = await make_strategy_version(async_client, auth_headers)
    queued = await enqueue(async_client, auth_headers, version_id, instruments["XNAS:AAPL"].id)

    finished = asyncio.Event()
    execute = worker.execute_job

    async def execute_and_signal(job_id):
        await execute(job_id)
        finished.set()

    monkeypatch.setattr(worker, "execute_job", execute_and_signal)
    task = asyncio.create_task(worker.run_forever())
    await asyncio.wait_for(finished.wait(), timeout=20)
    worker.stop()
    await asyncio.wait_for(task, timeout=5)

    assert (await fetch(async_client, auth_headers, queued.json()["id"]))["status"] == "COMPLETED"


# ------------------------------------------------------------------ the matcher
async def test_matcher_fills_resting_orders_when_the_price_arrives(
    async_client: AsyncClient,
    make_user,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.set_quote("AAPL", "200")
    resting = []
    for _ in range(2):  # two users, to show the matcher covers every portfolio
        _, headers = await make_user()
        portfolio = await open_portfolio(async_client, headers)
        order = await place(
            async_client, headers, portfolio["id"], aapl.id, "BUY", "10", limit_price="190"
        )
        assert order.json()["status"] == "OPEN"
        resting.append((headers, portfolio["id"], order.json()["id"]))

    assert await worker.match_open_orders() == 0  # 200 is above the limit

    fake_provider.set_quote("AAPL", "185")
    assert await worker.match_open_orders() == 2
    for headers, portfolio_id, order_id in resting:
        order = (
            await async_client.get(
                f"{PORTFOLIOS}/{portfolio_id}/orders/{order_id}", headers=headers
            )
        ).json()
        assert order["status"] == "FILLED"
        ledger = (
            await async_client.get(
                f"{PORTFOLIOS}/{portfolio_id}/ledger/balances", headers=headers
            )
        ).json()
        assert ledger["is_balanced"] and ledger["is_reconciled"]

    assert await worker.match_open_orders() == 0  # nothing left to do


async def test_matcher_leaves_orders_open_when_no_price_is_available(
    async_client: AsyncClient,
    auth_headers,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
):
    aapl = instruments["XNAS:AAPL"]
    fake_provider.set_quote("AAPL", "200")
    portfolio = await open_portfolio(async_client, auth_headers)
    order = await place(
        async_client, auth_headers, portfolio["id"], aapl.id, "BUY", "10", limit_price="190"
    )

    fake_provider.quotes.clear()
    fake_provider.error = ProviderConnectionError("provider is down")
    assert await worker.match_open_orders() == 0

    still = (
        await async_client.get(
            f"{PORTFOLIOS}/{portfolio['id']}/orders/{order.json()['id']}", headers=auth_headers
        )
    ).json()
    assert still["status"] == "OPEN"


async def test_a_failure_in_one_portfolio_does_not_stop_the_others(
    async_client: AsyncClient,
    make_user,
    instruments,
    fake_provider: FakeProvider,
    worker: Worker,
    monkeypatch,
):
    from app.trading.orders import OrderService

    aapl = instruments["XNAS:AAPL"]
    fake_provider.set_quote("AAPL", "200")
    portfolios = []
    for _ in range(2):
        _, headers = await make_user()
        portfolio = await open_portfolio(async_client, headers)
        await place(async_client, headers, portfolio["id"], aapl.id, "BUY", "10", limit_price="190")
        portfolios.append(portfolio["id"])
    fake_provider.set_quote("AAPL", "185")

    original = OrderService.process_open_orders
    doomed = uuid.UUID(portfolios[0])

    async def flaky(self, *, user_id, portfolio_id):
        if portfolio_id == doomed:
            raise RuntimeError("unexpected")
        return await original(self, user_id=user_id, portfolio_id=portfolio_id)

    monkeypatch.setattr(OrderService, "process_open_orders", flaky)
    assert await worker.match_open_orders() == 1


@pytest.mark.parametrize("embedded", [True, False])
async def test_the_api_starts_the_worker_only_when_embedded(embedded, monkeypatch):
    from app import main

    started = []

    class Recorder:
        def __init__(self, *_):
            pass

        async def run_forever(self):
            started.append(True)

        def stop(self):
            pass

    monkeypatch.setattr(settings, "worker_embedded", embedded)
    monkeypatch.setattr(main, "Worker", Recorder)

    class _Engine:
        async def dispose(self):
            pass

    monkeypatch.setattr(main, "engine", _Engine())
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
    assert started == ([True] if embedded else [])
