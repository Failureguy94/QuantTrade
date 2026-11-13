"""Background work: queued backtests and resting limit orders.

Run it inside the API process (``WORKER_EMBEDDED=true``, the default) or on its own::

    python -m app.worker

Jobs are rows in ``simulation_jobs``. A worker claims one with
``SELECT ... FOR UPDATE SKIP LOCKED``, so any number of workers can run side by side
without taking the same job, and no queue service is needed (ADR-018).
"""

import asyncio
import logging
import signal
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backtesting.service import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    BacktestService,
)
from app.core.config import settings
from app.market_data.base import MarketDataProvider
from app.market_data.service import MarketDataService
from app.models.portfolio import Order, Portfolio
from app.models.research import Experiment, SimulationJob
from app.trading.orders import OrderService, OrderStatus
from app.trading.quotes import QuoteService

logger = logging.getLogger(__name__)

_INTERNAL_ERROR = "The run failed because of an internal error."
_ABANDONED = "The run was interrupted (the worker stopped) and did not finish. Run it again."


class Worker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: MarketDataProvider,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        self._stop = asyncio.Event()

    # --------------------------------------------------------------------- jobs
    async def claim_job(self) -> uuid.UUID | None:
        """Take the oldest queued job, marking it ``RUNNING``. ``None`` if there is none."""
        async with self._sessions() as session:
            job = (
                await session.execute(
                    select(SimulationJob)
                    .where(SimulationJob.status == STATUS_QUEUED)
                    .order_by(SimulationJob.created_at, SimulationJob.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if job is None:
                return None
            job.status = STATUS_RUNNING
            await session.commit()
            return job.id

    async def execute_job(self, job_id: uuid.UUID) -> None:
        """Run a claimed job to ``COMPLETED`` or ``FAILED``. Never raises."""
        try:
            async with self._sessions() as session:
                job = await session.get(SimulationJob, job_id, populate_existing=True)
                if job is None:
                    return
                experiment = (
                    await session.get(Experiment, job.experiment_id, populate_existing=True)
                    if job.experiment_id
                    else None
                )
                if experiment is None:
                    job.status = STATUS_FAILED
                    job.error_message = "The experiment was deleted before it ran."
                    await session.commit()
                    return
                service = BacktestService(session, MarketDataService(session, self._provider))
                await service.execute(experiment, job)
                await session.commit()
                logger.info("Job %s finished: %s", job_id, job.status)
        except Exception:
            logger.exception("Job %s crashed", job_id)
            await self._fail(job_id, _INTERNAL_ERROR)

    async def _fail(self, job_id: uuid.UUID, message: str) -> None:
        async with self._sessions() as session:
            job = await session.get(SimulationJob, job_id, populate_existing=True)
            if job is None:
                return
            now = datetime.now(UTC)
            job.status = STATUS_FAILED
            job.error_message = message
            if job.experiment_id:
                await session.execute(
                    update(Experiment)
                    .where(Experiment.id == job.experiment_id)
                    .values(status=STATUS_FAILED, error_message=message, completed_at=now)
                )
            await session.commit()

    async def run_pending_jobs(self, limit: int = 10) -> int:
        """Claim and run queued jobs one after another. Returns how many ran."""
        ran = 0
        while ran < limit and not self._stop.is_set():
            job_id = await self.claim_job()
            if job_id is None:
                break
            await self.execute_job(job_id)
            ran += 1
        return ran

    async def fail_abandoned_jobs(self) -> int:
        """Fail jobs left ``RUNNING`` by a worker that died.

        A job is abandoned when it has been running longer than ``JOB_TIMEOUT_MINUTES``
        without an update. It is failed rather than retried: a job that kills its
        worker would otherwise do so forever.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=settings.job_timeout_minutes)
        async with self._sessions() as session:
            stale = (
                (
                    await session.execute(
                        select(SimulationJob.id)
                        .where(
                            SimulationJob.status == STATUS_RUNNING,
                            SimulationJob.updated_at < cutoff,
                        )
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
        for job_id in stale:
            logger.warning("Failing abandoned job %s", job_id)
            await self._fail(job_id, _ABANDONED)
        return len(stale)

    # ------------------------------------------------------------------- orders
    async def match_open_orders(self) -> int:
        """Re-price every resting limit order. Returns how many orders changed state.

        Each portfolio is handled in its own transaction, so a failure in one does
        not hold back the others.
        """
        async with self._sessions() as session:
            portfolios = (
                await session.execute(
                    select(Portfolio.id, Portfolio.user_id)
                    .join(Order, Order.portfolio_id == Portfolio.id)
                    .where(Order.status == OrderStatus.OPEN.value)
                    .distinct()
                )
            ).all()

        changed = 0
        for portfolio_id, user_id in portfolios:
            if self._stop.is_set():
                break
            try:
                async with self._sessions() as session:
                    quotes = QuoteService(session, provider=self._provider)
                    orders = await OrderService(session, quotes).process_open_orders(
                        user_id=user_id, portfolio_id=portfolio_id
                    )
                    await session.commit()
                    changed += len(orders)
            except Exception:
                logger.exception("Matching orders failed for portfolio %s", portfolio_id)
        if changed:
            logger.info("Matcher changed %d order(s)", changed)
        return changed

    # --------------------------------------------------------------------- loop
    def stop(self) -> None:
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def run_forever(self) -> None:
        """Poll for jobs and periodically match orders until :meth:`stop` is called."""
        logger.info("Worker started")
        loop = asyncio.get_running_loop()
        next_match = loop.time()
        next_sweep = loop.time()
        while not self._stop.is_set():
            try:
                now = loop.time()
                if now >= next_sweep:
                    await self.fail_abandoned_jobs()
                    next_sweep = now + 60.0
                ran = await self.run_pending_jobs()
                if now >= next_match:
                    await self.match_open_orders()
                    next_match = now + settings.matcher_interval_seconds
            except Exception:
                # Typically the database being unreachable. Keep going.
                logger.exception("Worker iteration failed")
                ran = 0
            if not ran:
                await self._sleep(settings.worker_poll_seconds)
        logger.info("Worker stopped")


async def _main() -> None:
    from app.api.deps import get_market_data_provider
    from app.core.logging import configure_logging, configure_sentry
    from app.db.session import AsyncSessionLocal, engine

    configure_logging()
    configure_sentry()
    worker = Worker(AsyncSessionLocal, get_market_data_provider())
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, worker.stop)
    try:
        await worker.run_forever()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
