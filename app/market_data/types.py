"""Core domain types, enums, and data models for Market Data Engine."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class DataInterval(StrEnum):
    """Supported standardized bar intervals."""
    ONE_MINUTE = "1m"
    TWO_MINUTES = "2m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"
    FIVE_DAYS = "5d"
    ONE_WEEK = "1wk"
    ONE_MONTH = "1mo"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return any(item.value == value for item in cls)

    @classmethod
    def get_supported_list(cls) -> list[str]:
        return [item.value for item in cls]


class PriceAdjustmentMode(StrEnum):
    """Explicit price adjustment mode. Never silently mixed."""
    RAW = "RAW"
    SPLIT_AND_DIVIDEND = "SPLIT_AND_DIVIDEND"
    SPLIT_ONLY = "SPLIT_ONLY"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capability declaration for market data providers."""
    name: str
    description: str
    supported_intervals: list[str]
    supports_adjusted_close: bool
    supports_unadjusted: bool
    supports_intraday: bool
    max_intraday_days: int | None = None
    is_realtime: bool = False
    is_exchange_grade: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "supported_intervals": self.supported_intervals,
            "supports_adjusted_close": self.supports_adjusted_close,
            "supports_unadjusted": self.supports_unadjusted,
            "supports_intraday": self.supports_intraday,
            "max_intraday_days": self.max_intraday_days,
            "is_realtime": self.is_realtime,
            "is_exchange_grade": self.is_exchange_grade,
        }


@dataclass(frozen=True)
class OHLCVRecord:
    """
    Provider-independent, normalized representation of a single OHLCV bar.
    - Financial prices represented with Decimal.
    - Timestamps strictly timezone-aware in UTC.
    - Volume is an integer (BigInteger compatible).
    """
    instrument_id: uuid.UUID
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    currency: str
    provider: str
    interval: str
    adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND
    adjusted_close: Decimal | None = None
    retrieval_time: datetime = datetime.now(UTC)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError(f"Timestamp must be timezone-aware (UTC), got naive {self.timestamp}")
