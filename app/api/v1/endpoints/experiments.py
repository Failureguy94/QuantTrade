"""Backtest experiments."""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_market_data_service
from app.backtesting.service import BacktestService
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.market_data.service import MarketDataService
from app.models.identity import User
from app.models.research import Experiment, SimulationJob
from app.schemas.experiment import (
    BacktestRequest,
    ExperimentRead,
    ExperimentSummary,
    SimulationJobRead,
)

router = APIRouter()


async def _get_owned_experiment(
    db: AsyncSession, user_id: uuid.UUID, experiment_id: uuid.UUID
) -> Experiment:
    experiment = (
        await db.execute(
            select(Experiment).where(
                Experiment.id == experiment_id, Experiment.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if experiment is None:
        raise NotFoundError("Experiment not found")
    return experiment


def _summary(experiment: Experiment) -> ExperimentSummary:
    summary = ExperimentSummary.model_validate(experiment)
    summary.metrics = (experiment.results or {}).get("metrics")
    return summary


def _detail(experiment: Experiment) -> ExperimentRead:
    detail = ExperimentRead.model_validate(experiment)
    detail.metrics = (experiment.results or {}).get("metrics")
    return detail


@router.post("/backtests", response_model=ExperimentRead, status_code=status.HTTP_201_CREATED)
async def run_backtest(
    payload: BacktestRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    market_data: MarketDataService = Depends(get_market_data_service),
):
    """Run a backtest of a strategy version on one instrument.

    The run executes within the request. The experiment is always stored: check
    ``status`` — ``COMPLETED`` carries ``results``, ``FAILED`` carries
    ``error_message`` (for example when the provider returned no data).
    """
    experiment = await BacktestService(db, market_data).run_backtest(current_user.id, payload)
    await db.commit()
    return _detail(experiment)


@router.get("", response_model=list[ExperimentSummary])
async def list_experiments(
    status_filter: str | None = Query(default=None, alias="status"),
    strategy_version_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List experiments, newest first. Results are summarised to their metrics."""
    stmt = (
        select(Experiment)
        .where(Experiment.user_id == current_user.id)
        .order_by(Experiment.created_at.desc(), Experiment.id)
        .limit(limit)
        .offset(offset)
    )
    if status_filter:
        stmt = stmt.where(Experiment.status == status_filter.upper())
    if strategy_version_id:
        stmt = stmt.where(Experiment.strategy_version_id == strategy_version_id)
    return [_summary(experiment) for experiment in (await db.execute(stmt)).scalars()]


@router.get("/{experiment_id}", response_model=ExperimentRead)
async def get_experiment(
    experiment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return _detail(await _get_owned_experiment(db, current_user.id, experiment_id))


@router.get("/{experiment_id}/jobs", response_model=list[SimulationJobRead])
async def list_experiment_jobs(
    experiment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    experiment = await _get_owned_experiment(db, current_user.id, experiment_id)
    stmt = (
        select(SimulationJob)
        .where(SimulationJob.experiment_id == experiment.id)
        .order_by(SimulationJob.created_at)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalars().all()


@router.delete("/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experiment(
    experiment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an experiment. Its job records and risk reports are kept, unlinked."""
    experiment = await _get_owned_experiment(db, current_user.id, experiment_id)
    await db.delete(experiment)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
