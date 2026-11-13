"""A market data provider that makes its prices up.

For local development, demos and automated browser tests, where depending on a live
feed would make runs slow, flaky and different every day. Prices follow a seeded
random walk per symbol, so the same symbol always has the same history: the bar for a
given day does not depend on which range was asked for or when.

Never a source of real prices. Production refuses to start with it (see Settings).
"""

import asyncio
import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from app.market_data.base import MarketDataProvider
from app.market_data.exceptions import NoDataFoundError
from app.market_data.types import OHLCVRecord, PriceAdjustmentMode, ProviderCapabilities

PROVIDER_NAME = "SYNTHETIC"
_EPOCH = date(2015, 1, 1)
_QUANTUM = Decimal("0.000001")
_RESAMPLE_RULES = {"1wk": "W-MON", "1mo": "MS"}


def _seed(symbol: str) -> int:
    return int.from_bytes(hashlib.sha256(symbol.upper().encode()).digest()[:8], "big")


def _daily_frame(symbol: str, today: date) -> pd.DataFrame:
    """Every weekday bar from the epoch to ``today``, identical on every call."""
    days = pd.bdate_range(_EPOCH, today, tz="UTC")
    rng = np.random.default_rng(_seed(symbol))
    count = len(days)
    # Per-symbol character: starting price, drift and volatility.
    start_price = float(rng.uniform(20, 400))
    drift = float(rng.uniform(0.0001, 0.0006))
    volatility = float(rng.uniform(0.009, 0.022))

    returns = rng.normal(drift, volatility, count)
    close = start_price * np.exp(np.cumsum(returns))
    gap = rng.normal(0, volatility / 3, count)
    open_ = np.concatenate(([start_price], close[:-1])) * np.exp(gap)
    spread = np.abs(rng.normal(0, volatility / 2, count))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.integers(500_000, 5_000_000, count)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=days
    )


def _resample(frame: pd.DataFrame, interval: str) -> pd.DataFrame:
    if interval == "1d":
        return frame
    rule = _RESAMPLE_RULES[interval]
    grouped = frame.resample(rule, label="left", closed="left")
    return grouped.agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def _price(value: float) -> Decimal:
    return Decimal(str(value)).quantize(_QUANTUM)


class SyntheticProvider(MarketDataProvider):
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=PROVIDER_NAME,
            description="Made-up prices from a seeded random walk. Not real market data.",
            supported_intervals=["1d", "1wk", "1mo"],
            supports_adjusted_close=True,
            supports_unadjusted=True,
            supports_intraday=False,
        )

    def resolve_symbol(self, exchange_mic: str, ticker: str) -> str | None:
        # Qualified by exchange, so the same ticker on two exchanges differs.
        return f"{exchange_mic}:{ticker}"

    def _frame(self, provider_symbol: str, start: datetime, end: datetime, interval: str):
        if interval not in self.capabilities.supported_intervals:
            raise NoDataFoundError(
                f"The synthetic provider has no {interval} bars", provider=self.name
            )
        frame = _resample(_daily_frame(provider_symbol, datetime.now(UTC).date()), interval)
        return frame[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]

    async def fetch_historical_bars(
        self,
        provider_symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
        adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        instrument_id: uuid.UUID | None = None,
        currency: str = "USD",
    ) -> list[OHLCVRecord]:
        frame = await asyncio.to_thread(self._frame, provider_symbol, start, end, interval)
        if frame.empty:
            raise NoDataFoundError(
                f"No synthetic prices for {provider_symbol} in the requested range",
                provider=self.name,
            )
        instrument_id = instrument_id or uuid.uuid4()
        records = []
        for timestamp, row in frame.iterrows():
            open_, close = _price(row["open"]), _price(row["close"])
            records.append(
                OHLCVRecord(
                    instrument_id=instrument_id,
                    timestamp=timestamp.to_pydatetime(),
                    open=open_,
                    # Quantising can nudge a bound past the body; keep the bar valid.
                    high=max(_price(row["high"]), open_, close),
                    low=min(_price(row["low"]), open_, close),
                    close=close,
                    adjusted_close=close,
                    volume=int(row["volume"]),
                    currency=currency,
                    provider=self.name,
                    interval=interval,
                    adjustment_mode=adjustment_mode,
                )
            )
        return records

    async def get_historical_data(
        self,
        provider_symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
        adjusted: bool = True,
    ) -> pd.DataFrame:
        frame = await asyncio.to_thread(self._frame, provider_symbol, start, end, interval)
        return frame.rename(columns=str.capitalize)
