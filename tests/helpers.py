"""Builders and fakes shared by the test suites."""

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd

from app.market_data.base import MarketDataProvider
from app.market_data.exceptions import NoDataFoundError
from app.market_data.types import OHLCVRecord, PriceAdjustmentMode, ProviderCapabilities
from app.trading.quotes import QuoteService

PRICE_QUANTUM = Decimal("0.000001")


def make_bars(
    closes: list[float],
    *,
    start: date = date(2024, 1, 1),
    end: date | None = None,
    instrument_id: uuid.UUID | None = None,
    opens: list[float] | None = None,
    currency: str = "USD",
    adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
) -> list[OHLCVRecord]:
    """Build weekday daily bars. Each open defaults to the previous close.

    Bars run forward from ``start``, or backward from ``end`` when it is given, so
    the last bar falls on ``end`` (or the weekday before it).
    """
    instrument_id = instrument_id or uuid.uuid4()
    if end is not None:
        start, remaining = end, len(closes)
        while True:
            if start.weekday() < 5:
                remaining -= 1
                if remaining <= 0:
                    break
            start -= timedelta(days=1)
    bars: list[OHLCVRecord] = []
    day = start
    for index, close in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        if opens is not None:
            open_price = opens[index]
        else:
            open_price = closes[index - 1] if index else close
        bars.append(
            OHLCVRecord(
                instrument_id=instrument_id,
                timestamp=datetime(day.year, day.month, day.day, tzinfo=UTC),
                open=Decimal(str(open_price)).quantize(PRICE_QUANTUM),
                high=Decimal(str(max(open_price, close) * 1.01)).quantize(PRICE_QUANTUM),
                low=Decimal(str(min(open_price, close) * 0.99)).quantize(PRICE_QUANTUM),
                close=Decimal(str(close)).quantize(PRICE_QUANTUM),
                adjusted_close=Decimal(str(close)).quantize(PRICE_QUANTUM),
                volume=1_000_000,
                currency=currency,
                provider="YAHOO",
                interval="1d",
                adjustment_mode=adjustment_mode,
            )
        )
        day += timedelta(days=1)
    return bars


def wave_closes(count: int, *, base: float = 100.0, drift: float = 0.05) -> list[float]:
    """Deterministic trending, oscillating series that makes every strategy trade."""
    return [
        round(base + drift * i + 12.0 * math.sin(i / 9.0) + 3.0 * math.sin(i / 2.3), 4)
        for i in range(count)
    ]


class FakeProvider(MarketDataProvider):
    """In-memory stand-in for Yahoo Finance. Keyed by provider symbol."""

    def __init__(self) -> None:
        self.quotes: dict[str, Decimal] = {}
        self.history: dict[str, list[OHLCVRecord]] = {}
        self.error: Exception | None = None
        self.calls = 0

    @property
    def name(self) -> str:
        return "YAHOO"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="YAHOO",
            description="fake",
            supported_intervals=["1d", "1wk", "1mo"],
            supports_adjusted_close=True,
            supports_unadjusted=True,
            supports_intraday=False,
        )

    def resolve_symbol(self, exchange_mic: str, ticker: str) -> str | None:
        return ticker

    def set_quote(self, symbol: str, price: str | Decimal) -> None:
        self.quotes[symbol] = Decimal(str(price))
        QuoteService.clear_cache()

    async def fetch_historical_bars(
        self,
        provider_symbol,
        start,
        end,
        interval="1d",
        adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        instrument_id=None,
        currency="USD",
    ) -> list[OHLCVRecord]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        instrument_id = instrument_id or uuid.uuid4()

        if provider_symbol in self.quotes and adjustment_mode is PriceAdjustmentMode.RAW:
            today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            price = self.quotes[provider_symbol]
            return [
                OHLCVRecord(
                    instrument_id=instrument_id,
                    timestamp=today,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1000,
                    currency=currency,
                    provider=self.name,
                    interval=interval,
                    adjustment_mode=adjustment_mode,
                )
            ]

        bars = [
            OHLCVRecord(
                instrument_id=instrument_id,
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                adjusted_close=bar.adjusted_close,
                volume=bar.volume,
                currency=currency,
                provider=self.name,
                interval=interval,
                adjustment_mode=adjustment_mode,
            )
            for bar in self.history.get(provider_symbol, [])
            if start <= bar.timestamp <= end
        ]
        if not bars:
            raise NoDataFoundError(
                f"No market data returned for symbol '{provider_symbol}'", provider=self.name
            )
        return bars

    async def get_historical_data(self, provider_symbol, start, end, interval="1d", adjusted=True):
        return pd.DataFrame()
