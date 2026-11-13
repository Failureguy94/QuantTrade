"""Market Data API endpoints.

Every route here requires an authenticated user (ADR-014): the historical endpoint
can trigger upstream provider requests, which must be attributable to an account.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.api.errors import market_data_http_exception
from app.api.limits import market_data_rate_limit
from app.api.pagination import set_total_count
from app.db.session import get_db
from app.market_data.exceptions import MarketDataError
from app.market_data.service import MarketDataService
from app.market_data.types import PriceAdjustmentMode
from app.models.identity import Exchange, Instrument
from app.schemas.market_data import (
    HistoricalCandleRead,
    HistoricalDataResponse,
    MarketDataInstrumentRead,
    ProviderSummary,
)

# Declared on the router so an endpoint added to this module is protected by default.
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/instruments", response_model=list[MarketDataInstrumentRead])
async def list_market_data_instruments(
    response: Response,
    exchange_mic: str | None = Query(
        default=None,
        description="Filter by exchange Market Identifier Code (e.g. XNSE, XNAS)",
    ),
    search: str | None = Query(default=None, description="Search ticker symbol or name"),
    asset_type: str | None = Query(
        default=None, description="Filter by asset type (e.g. EQUITY, ETF)"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List market data instruments with exchange metadata and provider mappings."""
    stmt = (
        select(Instrument)
        .join(Instrument.exchange)
        .options(
            selectinload(Instrument.exchange),
            selectinload(Instrument.provider_symbols),
        )
        .where(Instrument.is_active.is_(True))
        .order_by(Instrument.symbol, Instrument.id)
        .limit(limit)
        .offset(offset)
    )

    if exchange_mic:
        stmt = stmt.where(Exchange.mic == exchange_mic.upper())
    if asset_type:
        stmt = stmt.where(Instrument.asset_type == asset_type.upper())
    if search:
        search_pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Instrument.symbol.ilike(search_pattern),
                Instrument.name.ilike(search_pattern),
            )
        )

    await set_total_count(response, db, stmt)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/providers", response_model=list[ProviderSummary])
async def list_market_data_providers(
    db: AsyncSession = Depends(get_db),
):
    """Report registered market data providers, supported intervals, and capabilities."""
    service = MarketDataService(db=db)
    capabilities = service.list_providers()
    return [
        ProviderSummary(
            name=c.name,
            description=c.description,
            supported_intervals=c.supported_intervals,
            supports_adjusted_close=c.supports_adjusted_close,
            supports_unadjusted=c.supports_unadjusted,
            supports_intraday=c.supports_intraday,
            max_intraday_days=c.max_intraday_days,
            is_realtime=c.is_realtime,
            is_exchange_grade=c.is_exchange_grade,
        )
        for c in capabilities
    ]


@router.get(
    "/{instrument_id}/historical",
    response_model=HistoricalDataResponse,
    dependencies=[Depends(market_data_rate_limit)],
)
async def get_historical_market_data(
    instrument_id: uuid.UUID,
    start: datetime | None = Query(default=None, description="Start date/time (UTC)"),
    end: datetime | None = Query(default=None, description="End date/time (UTC)"),
    interval: str = Query(default="1d", description="Bar interval (e.g. 1m, 5m, 1h, 1d)"),
    adjusted: bool = Query(
        default=True,
        description="Whether prices are adjusted for splits and dividends",
    ),
    provider: str = Query(default="YAHOO", description="Upstream market data provider"),
    force_refresh: bool = Query(
        default=False,
        description="Bypass local cache and re-fetch from upstream",
    ),
    limit: int = Query(default=1000, ge=1, le=5000, description="Max bars to return"),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch historical OHLCV data for an instrument with strict validation
    and cryptographic reproducibility fingerprinting.
    """
    adjustment_mode = (
        PriceAdjustmentMode.SPLIT_AND_DIVIDEND if adjusted else PriceAdjustmentMode.RAW
    )
    service = MarketDataService(db=db)

    try:
        instrument, records, fingerprint, warnings = await service.get_historical_market_data(
            instrument_id=instrument_id,
            interval=interval,
            adjustment_mode=adjustment_mode,
            start=start,
            end=end,
            provider_name=provider,
            force_fetch=force_refresh,
            limit=limit,
        )
    except MarketDataError as exc:
        raise market_data_http_exception(exc) from exc

    # Fetched candles are written through the session; without a commit they
    # would be discarded when the request ends and re-downloaded every time.
    await db.commit()

    candle_reads = [
        HistoricalCandleRead(
            timestamp=r.timestamp,
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            adjusted_close=r.adjusted_close,
            volume=r.volume,
        )
        for r in records
    ]

    return HistoricalDataResponse(
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        exchange_mic=instrument.exchange.mic,
        currency=instrument.currency_code,
        interval=interval,
        adjustment_mode=adjustment_mode.value,
        data_fingerprint=fingerprint,
        count=len(candle_reads),
        candles=candle_reads,
        warnings=warnings,
    )
