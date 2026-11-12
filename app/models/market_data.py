"""Market data persistence models."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKey


class MarketDataCandle(UUIDPrimaryKey, Base):
    """
    Normalized historical OHLCV bar representation.
    
    Guarantees:
    - Accurate financial price precision using Numeric(20, 6).
    - Large volume storage using BigInteger (64-bit int).
    - Timezone-aware UTC timestamp.
    - Deduplication via composite unique constraint on:
      (instrument_id, interval, adjustment_mode, timestamp).
    - Traceability metadata: provider, retrieval_time, adjustment_mode.
    """
    __tablename__ = "market_data_candles"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "interval",
            "adjustment_mode",
            "timestamp",
            name="uq_candle_inst_intv_adj_ts",
        ),
        Index(
            "ix_candles_query",
            "instrument_id",
            "interval",
            "adjustment_mode",
            "timestamp",
        ),
        CheckConstraint("high >= low", name="ck_candle_high_ge_low"),
        CheckConstraint("open >= low AND open <= high", name="ck_candle_open_within_range"),
        CheckConstraint("close >= low AND close <= high", name="ck_candle_close_within_range"),
        CheckConstraint(
            "open > 0 AND high > 0 AND low > 0 AND close > 0",
            name="ck_candle_positive_prices",
        ),
        CheckConstraint("volume >= 0", name="ck_candle_non_negative_volume"),
    )

    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    adjusted_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    interval: Mapped[str] = mapped_column(String(16), nullable=False)
    adjustment_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="SPLIT_AND_DIVIDEND",
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    instrument = relationship("Instrument", backref="candles")
