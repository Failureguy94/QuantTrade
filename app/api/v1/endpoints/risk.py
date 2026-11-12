"""Risk reports for portfolios and completed backtests."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_market_data_service
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.market_data.service import MarketDataService
from app.models.identity import User
from app.models.research import RiskReport
from app.risk.service import RiskParameters, RiskService
from app.schemas.risk import RiskReportCreate, RiskReportRead

router = APIRouter()


@router.post("/reports", response_model=RiskReportRead, status_code=status.HTTP_201_CREATED)
async def create_risk_report(
    payload: RiskReportCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    market_data: MarketDataService = Depends(get_market_data_service),
):
    """Compute and store Value-at-Risk and expected shortfall.

    For a portfolio, the report covers its current holdings over the last
    ``lookback_days`` sessions. For an experiment, it covers the returns of the
    backtest's equity curve.
    """
    service = RiskService(db, market_data)
    params = RiskParameters(
        method=payload.method,
        confidence_level=payload.confidence_level,
        horizon_days=payload.horizon_days,
        lookback_days=payload.lookback_days,
        simulations=payload.simulations,
        random_seed=payload.random_seed,
    )
    if payload.portfolio_id is not None:
        report = await service.create_portfolio_report(
            current_user.id, payload.portfolio_id, params
        )
    else:
        assert payload.experiment_id is not None  # enforced by the schema
        report = await service.create_experiment_report(
            current_user.id, payload.experiment_id, params
        )
    await db.commit()
    return report


@router.get("/reports", response_model=list[RiskReportRead])
async def list_risk_reports(
    portfolio_id: uuid.UUID | None = Query(default=None),
    experiment_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(RiskReport)
        .where(RiskReport.user_id == current_user.id)
        .order_by(RiskReport.created_at.desc(), RiskReport.id)
        .limit(limit)
        .offset(offset)
    )
    if portfolio_id:
        stmt = stmt.where(RiskReport.portfolio_id == portfolio_id)
    if experiment_id:
        stmt = stmt.where(RiskReport.experiment_id == experiment_id)
    return (await db.execute(stmt)).scalars().all()


@router.get("/reports/{report_id}", response_model=RiskReportRead)
async def get_risk_report(
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    report = (
        await db.execute(
            select(RiskReport).where(
                RiskReport.id == report_id, RiskReport.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError("Risk report not found")
    return report
