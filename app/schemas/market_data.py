"""Pydantic schemas for Market Data API requests and responses."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ExchangeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mic: str
    name: str
    country_code: str
    timezone: str
    currency_code: str
    calendar_code: str


class ProviderSymbolSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    provider_symbol: str
    is_primary: bool


class MarketDataInstrumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    name: str
    asset_type: str
    currency_code: str
    isin: str | None = None
    exchange: ExchangeSummary
    provider_symbols: list[ProviderSymbolSummary] = Field(default_factory=list)


class HistoricalCandleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal | None = None
    volume: int


class HistoricalDataResponse(BaseModel):
    instrument_id: uuid.UUID
    symbol: str
    exchange_mic: str
    currency: str
    interval: str
    adjustment_mode: str
    data_fingerprint: str
    count: int
    candles: list[HistoricalCandleRead]
    warnings: list[str] = Field(default_factory=list)


class ProviderSummary(BaseModel):
    name: str
    description: str
    supported_intervals: list[str]
    supports_adjusted_close: bool
    supports_unadjusted: bool
    supports_intraday: bool
    max_intraday_days: int | None = None
    is_realtime: bool = False
    is_exchange_grade: bool = False
