"""Runs backtests as reproducible experiments (ADR-007)."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, time
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting.engine import ENGINE_VERSION, BacktestConfig, BacktestError, run_backtest
from app.backtesting.metrics import PERIODS_PER_YEAR
from app.backtesting.strategies import (
    InvalidStrategyParametersError,
    UnknownStrategyTypeError,
    generate_signals,
    validate_parameters,
)
from app.core.errors import NotFoundError, UnprocessableError
from app.market_data.exceptions import MarketDataError
from app.market_data.service import MarketDataService
from app.market_data.types import OHLCVRecord, PriceAdjustmentMode
from app.models.identity import Instrument
from app.models.research import Experiment, SimulationJob, Strategy, StrategyVersion
from app.schemas.experiment import BacktestRequest

logger = logging.getLogger(__name__)

EXPERIMENT_TYPE_BACKTEST = "BACKTEST"
MAX_BACKTEST_BARS = 100_000


def _simulate(
    bars: list[OHLCVRecord],
    strategy_type: str,
    parameters: dict[str, Any],
    config: BacktestConfig,
) -> dict[str, Any]:
    """CPU-bound part of a backtest; runs in a worker thread."""
    closes = np.array([float(bar.close) for bar in bars], dtype=np.float64)
    signals = generate_signals(strategy_type, parameters, closes)
    return run_backtest(bars, signals, config).to_payload()


class BacktestService:
    def __init__(self, db: AsyncSession, market_data: MarketDataService) -> None:
        self.db = db
        self.market_data = market_data

    async def _load_strategy_version(
        self, user_id: uuid.UUID, strategy_version_id: uuid.UUID
    ) -> tuple[Strategy, StrategyVersion]:
        stmt = (
            select(Strategy, StrategyVersion)
            .join(StrategyVersion, StrategyVersion.strategy_id == Strategy.id)
            .where(StrategyVersion.id == strategy_version_id, Strategy.user_id == user_id)
        )
        row = (await self.db.execute(stmt)).one_or_none()
        if row is None:
            raise NotFoundError("Strategy version not found")
        return row[0], row[1]

    async def run_backtest(self, user_id: uuid.UUID, request: BacktestRequest) -> Experiment:
        """Run a backtest and persist it as an experiment.

        Problems with the request itself raise. Failures while running (no market
        data, provider outage, too few bars) are recorded on the experiment, which
        is returned with status ``FAILED`` so the attempt stays auditable.
        """
        strategy, version = await self._load_strategy_version(
            user_id, request.strategy_version_id
        )
        try:
            parameters = validate_parameters(strategy.strategy_type, version.parameters)
        except (UnknownStrategyTypeError, InvalidStrategyParametersError) as exc:
            raise UnprocessableError(
                f"Strategy version cannot be backtested: {exc}"
            ) from exc

        instrument = await self.db.get(Instrument, request.instrument_id)
        if instrument is None:
            raise NotFoundError("Instrument not found")

        adjustment_mode = (
            PriceAdjustmentMode.SPLIT_AND_DIVIDEND if request.adjusted else PriceAdjustmentMode.RAW
        )
        started_at = datetime.now(UTC)
        experiment = Experiment(
            user_id=user_id,
            strategy_version_id=version.id,
            experiment_type=EXPERIMENT_TYPE_BACKTEST,
            status="RUNNING",
            configuration={
                "request": request.model_dump(mode="json"),
                "strategy": {
                    "id": str(strategy.id),
                    "type": strategy.strategy_type,
                    "version": version.version,
                    "parameters": parameters,
                },
                "instrument": {
                    "id": str(instrument.id),
                    "symbol": instrument.symbol,
                    "currency": instrument.currency_code,
                },
                "execution": {
                    "fill": "next bar open",
                    "adjustment_mode": adjustment_mode.value,
                },
            },
            engine_version=ENGINE_VERSION,
            # Built-in strategies are deterministic: no random numbers are drawn.
            random_seed=None,
            started_at=started_at,
        )
        self.db.add(experiment)
        await self.db.flush()
        job = SimulationJob(
            user_id=user_id,
            experiment_id=experiment.id,
            job_type=EXPERIMENT_TYPE_BACKTEST,
            status="RUNNING",
            progress=0,
        )
        self.db.add(job)
        await self.db.flush()

        try:
            _, bars, fingerprint, data_warnings = (
                await self.market_data.get_covering_market_data(
                    instrument.id,
                    interval=request.interval,
                    adjustment_mode=adjustment_mode,
                    start=datetime.combine(request.start, time.min, tzinfo=UTC),
                    end=datetime.combine(request.end, time.max, tzinfo=UTC),
                    provider_name=request.provider,
                    limit=MAX_BACKTEST_BARS,
                )
            )
            experiment.data_fingerprint = fingerprint
            job.progress = 50

            config = BacktestConfig(
                initial_capital=request.initial_capital,
                commission_bps=request.commission_bps,
                slippage_bps=request.slippage_bps,
                position_size=request.position_size,
                allow_short=request.allow_short,
                fractional_shares=request.fractional_shares,
                periods_per_year=PERIODS_PER_YEAR[request.interval],
                risk_free_rate=request.risk_free_rate,
            )
            results = await asyncio.to_thread(
                _simulate, bars, strategy.strategy_type, parameters, config
            )
        except (MarketDataError, BacktestError) as exc:
            logger.info("Backtest %s failed: %s", experiment.id, exc)
            message = exc.message if isinstance(exc, MarketDataError) else str(exc)
            experiment.status = job.status = "FAILED"
            experiment.error_message = job.error_message = message
            experiment.completed_at = datetime.now(UTC)
            await self.db.flush()
            return experiment

        warnings = list(data_warnings)
        if not request.adjusted:
            warnings.append(
                "Unadjusted prices: splits and dividends appear as price jumps and "
                "distort returns."
            )
        results["warnings"] = warnings + results["warnings"]
        results["data"] = {
            "bars": len(bars),
            "first_bar": bars[0].timestamp.isoformat(),
            "last_bar": bars[-1].timestamp.isoformat(),
            "provider": bars[0].provider,
            "currency": instrument.currency_code,
        }

        experiment.results = results
        experiment.status = job.status = "COMPLETED"
        experiment.completed_at = datetime.now(UTC)
        job.progress = 100
        job.result_reference = f"experiments/{experiment.id}"
        await self.db.flush()
        return experiment
