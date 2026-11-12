"""Market data repository abstraction and PostgreSQL implementation."""

import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_data.fingerprint import compute_dataset_fingerprint
from app.market_data.types import OHLCVRecord, PriceAdjustmentMode
from app.models.market_data import MarketDataCandle


class MarketDataRepository(ABC):
    """
    Abstract storage repository for historical market data candles.
    
    Decouples callers (backtester, risk engine, API endpoints) from the underlying
    storage engine (PostgreSQL, ClickHouse, Parquet lake).
    """

    @abstractmethod
    async def get_candles(
        self,
        instrument_id: uuid.UUID,
        interval: str = "1d",
        adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 5000,
    ) -> list[OHLCVRecord]:
        """Retrieve historical candles ordered chronologically."""
        raise NotImplementedError

    @abstractmethod
    async def save_candles(
        self,
        records: Sequence[OHLCVRecord],
        upsert: bool = True,
    ) -> int:
        """Persist candles with deduplication. Returns number of rows inserted or updated."""
        raise NotImplementedError

    @abstractmethod
    async def get_fingerprint(
        self,
        instrument_id: uuid.UUID,
        interval: str = "1d",
        adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> str:
        """Compute cryptographic reproducibility fingerprint over retrieved records."""
        raise NotImplementedError


class SQLMarketDataRepository(MarketDataRepository):
    """
    Production-grade PostgreSQL repository implementing atomic bulk upserts and range queries.
    
    Tradeoff rationale:
    - PostgreSQL MVP provides immediate ACID relational integrity with instruments/exchanges,
      strict check constraints, zero additional infrastructure overhead, and sub-millisecond
      indexed lookups.
    - The repository boundary ensures a specialized columnar/time-series database can be introduced
      seamlessly in future high-throughput phases without refactoring research or trading code.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_candles(
        self,
        instrument_id: uuid.UUID,
        interval: str = "1d",
        adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 5000,
    ) -> list[OHLCVRecord]:
        stmt = (
            select(MarketDataCandle)
            .where(
                MarketDataCandle.instrument_id == instrument_id,
                MarketDataCandle.interval == interval,
                MarketDataCandle.adjustment_mode == adjustment_mode.value,
            )
            .order_by(MarketDataCandle.timestamp.asc())
            .limit(limit)
        )

        if start is not None:
            stmt = stmt.where(MarketDataCandle.timestamp >= start)
        if end is not None:
            stmt = stmt.where(MarketDataCandle.timestamp <= end)

        result = await self.session.execute(stmt)
        rows = result.scalars().all()

        records: list[OHLCVRecord] = []
        for row in rows:
            rec = OHLCVRecord(
                instrument_id=row.instrument_id,
                timestamp=row.timestamp,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                adjusted_close=row.adjusted_close,
                volume=row.volume,
                currency=row.currency,
                provider=row.provider,
                interval=row.interval,
                adjustment_mode=PriceAdjustmentMode(row.adjustment_mode),
                retrieval_time=row.retrieval_time,
            )
            records.append(rec)
        return records

    async def save_candles(
        self,
        records: Sequence[OHLCVRecord],
        upsert: bool = True,
    ) -> int:
        if not records:
            return 0

        # Build payload for insert
        values: list[dict] = []
        for rec in records:
            values.append({
                "id": uuid.uuid4(),
                "instrument_id": rec.instrument_id,
                "timestamp": rec.timestamp,
                "open": rec.open,
                "high": rec.high,
                "low": rec.low,
                "close": rec.close,
                "adjusted_close": rec.adjusted_close,
                "volume": rec.volume,
                "currency": rec.currency,
                "interval": rec.interval,
                "adjustment_mode": rec.adjustment_mode.value,
                "provider": rec.provider,
                "retrieval_time": rec.retrieval_time,
            })

        insert_stmt = insert(MarketDataCandle).values(values)

        if upsert:
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=["instrument_id", "interval", "adjustment_mode", "timestamp"],
                set_={
                    "open": insert_stmt.excluded.open,
                    "high": insert_stmt.excluded.high,
                    "low": insert_stmt.excluded.low,
                    "close": insert_stmt.excluded.close,
                    "adjusted_close": insert_stmt.excluded.adjusted_close,
                    "volume": insert_stmt.excluded.volume,
                    "provider": insert_stmt.excluded.provider,
                    "retrieval_time": insert_stmt.excluded.retrieval_time,
                },
            )
        else:
            stmt = insert_stmt.on_conflict_do_nothing(
                index_elements=["instrument_id", "interval", "adjustment_mode", "timestamp"]
            )

        result = cast(CursorResult, await self.session.execute(stmt))
        return result.rowcount

    async def get_fingerprint(
        self,
        instrument_id: uuid.UUID,
        interval: str = "1d",
        adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> str:
        candles = await self.get_candles(
            instrument_id=instrument_id,
            interval=interval,
            adjustment_mode=adjustment_mode,
            start=start,
            end=end,
            limit=100000,
        )
        return compute_dataset_fingerprint(candles)
