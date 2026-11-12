"""Yahoo Finance market data provider adapter.

Research and backtesting adapter only. Not a real-time or exchange-grade feed.

## Adjustment mode behaviour (yfinance-specific)

``SPLIT_AND_DIVIDEND`` — ``yfinance.download(auto_adjust=True)``
    yfinance adjusts *all* OHLCV columns (Open, High, Low, Close, Volume) for
    splits and dividends before returning them.  The main price columns
    therefore contain **adjusted** values, not raw prices.  ``adjusted_close``
    is set equal to ``close`` to signal that the entire series is adjusted.
    There is no separate "Adj Close" column in this mode.

``RAW`` — ``yfinance.download(auto_adjust=False)``
    yfinance returns raw (unadjusted) Open, High, Low, Close prices.  A
    separate "Adj Close" column is included; the adapter maps this to the
    ``OHLCVRecord.adjusted_close`` field so that callers can apply their own
    adjustment logic when needed.

``SPLIT_ONLY``
    yfinance does not expose a split-only adjustment mode.  The adapter
    treats this as ``RAW`` and emits a warning.  Callers requiring split-only
    adjusted prices must implement their own adjustment factor logic.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import yfinance as yf

from app.market_data.base import MarketDataProvider
from app.market_data.exceptions import (
    InstrumentNotFoundError,
    InvalidIntervalError,
    NoDataFoundError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.market_data.types import (
    OHLCVRecord,
    PriceAdjustmentMode,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)


class YahooFinanceProvider(MarketDataProvider):
    """
    Yahoo Finance provider adapter.
    Converts Yahoo Finance responses into normalized, validated domain models.
    """

    SUPPORTED_INTERVALS = [
        "1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"
    ]

    CAPABILITIES = ProviderCapabilities(
        name="YAHOO",
        description=(
            "Yahoo Finance historical market data feed for quantitative research and backtesting"
        ),
        supported_intervals=SUPPORTED_INTERVALS,
        supports_adjusted_close=True,
        supports_unadjusted=True,
        supports_intraday=True,
        max_intraday_days=60,
        is_realtime=False,
        is_exchange_grade=False,
    )

    @property
    def name(self) -> str:
        return "YAHOO"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self.CAPABILITIES

    def resolve_symbol(self, exchange_mic: str, ticker: str) -> str | None:
        clean_mic = exchange_mic.upper().strip()
        clean_ticker = ticker.upper().strip()

        if clean_mic in {"XNSE", "NSE"}:
            return f"{clean_ticker}.NS"
        if clean_mic in {"XBOM", "BSE"}:
            return f"{clean_ticker}.BO"
        if clean_mic in {"XNAS", "NASDAQ", "XNYS", "NYSE"}:
            # Handle class shares like BRK.A -> BRK-A in Yahoo
            return clean_ticker.replace(".", "-")
        return clean_ticker

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
        """Fetch historical candles, normalize into OHLCVRecord objects.

        Important: when ``adjustment_mode`` is ``SPLIT_AND_DIVIDEND``, yfinance
        returns *adjusted* Open, High, Low, and Close values in every column —
        not raw prices.  The returned ``OHLCVRecord.open/high/low/close`` fields
        therefore represent **split- and dividend-adjusted** values, and
        ``adjusted_close`` equals ``close``.

        When ``adjustment_mode`` is ``RAW``, the OHLC columns are raw
        (unadjusted) prices and ``adjusted_close`` contains the separately
        provided split-and-dividend adjusted close.

        When ``adjustment_mode`` is ``SPLIT_ONLY``, yfinance has no native
        support for this mode.  The adapter falls back to ``RAW`` behaviour and
        logs a warning; the records are stored with ``adjustment_mode=SPLIT_ONLY``
        so callers can distinguish them, but the OHLC values are raw.
        """
        if not self.supports_interval(interval):
            raise InvalidIntervalError(
                interval=interval,
                supported_intervals=self.SUPPORTED_INTERVALS,
                provider=self.name,
            )

        # yfinance only has two modes: auto_adjust=True (split+dividend) or False (raw).
        # SPLIT_ONLY is unsupported natively; fall back to RAW and warn.
        if adjustment_mode == PriceAdjustmentMode.SPLIT_ONLY:
            logger.warning(
                "YahooFinanceProvider does not support SPLIT_ONLY adjustment mode. "
                "Falling back to RAW (unadjusted) prices for symbol '%s'. "
                "Records will be stored with adjustment_mode=SPLIT_ONLY but OHLC "
                "values will be unadjusted. Apply split factors manually if needed.",
                provider_symbol,
            )
            auto_adjust = False
        else:
            auto_adjust = (adjustment_mode == PriceAdjustmentMode.SPLIT_AND_DIVIDEND)

        df = await asyncio.to_thread(
            self._download_dataframe,
            provider_symbol=provider_symbol,
            start=start,
            end=end,
            interval=interval,
            auto_adjust=auto_adjust,
        )

        return self._dataframe_to_records(
            df=df,
            instrument_id=instrument_id or uuid.uuid4(),
            provider_symbol=provider_symbol,
            interval=interval,
            currency=currency,
            adjustment_mode=adjustment_mode,
        )

    async def get_historical_data(
        self,
        provider_symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Backward-compatible DataFrame interface returning UTC-indexed OHLCV data."""
        if not self.supports_interval(interval):
            raise InvalidIntervalError(
                interval=interval,
                supported_intervals=self.SUPPORTED_INTERVALS,
                provider=self.name,
            )

        df = await asyncio.to_thread(
            self._download_dataframe,
            provider_symbol=provider_symbol,
            start=start,
            end=end,
            interval=interval,
            auto_adjust=adjusted,
        )
        return df

    def _download_dataframe(
        self,
        provider_symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
        auto_adjust: bool,
    ) -> pd.DataFrame:
        """Synchronous worker offloaded to thread pool."""
        try:
            raw = yf.download(
                provider_symbol,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=auto_adjust,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            self._normalize_exception(exc, provider_symbol)

        if raw is None or raw.empty:
            raise NoDataFoundError(
                f"No market data returned for symbol '{provider_symbol}' in range {start} to {end}",
                provider=self.name,
                details={"symbol": provider_symbol, "start": str(start), "end": str(end)},
            )

        # Handle MultiIndex columns (yfinance 0.2+)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        # Normalize column names: strip, lowercase, spaces → underscores.
        # e.g. yfinance "Adj Close" → "adj_close", "Open" → "open"
        raw.columns = [str(col).strip().lower().replace(" ", "_") for col in raw.columns]

        # Convert index to UTC DatetimeIndex
        raw.index = pd.to_datetime(raw.index, utc=True)
        raw.index.name = "timestamp"

        required = ["open", "high", "low", "close", "volume"]
        missing = set(required) - set(raw.columns)
        if missing:
            raise NoDataFoundError(
                f"Provider response missing required OHLCV columns: {sorted(missing)}",
                provider=self.name,
            )

        # Keep relevant columns.
        # "adj_close" only exists when auto_adjust=False (RAW / SPLIT_ONLY modes).
        # In SPLIT_AND_DIVIDEND mode (auto_adjust=True) yfinance does not emit a
        # separate Adj Close column — the OHLCV columns are already fully adjusted.
        cols = list(required)
        if "adj_close" in raw.columns and not auto_adjust:
            cols.append("adj_close")

        clean = raw[cols].dropna(subset=["open", "high", "low", "close"])
        if clean.empty:
            raise NoDataFoundError(
                f"All candles for '{provider_symbol}' had missing or NaN prices",
                provider=self.name,
            )
        return clean

    def _dataframe_to_records(
        self,
        df: pd.DataFrame,
        instrument_id: uuid.UUID,
        provider_symbol: str,
        interval: str,
        currency: str,
        adjustment_mode: PriceAdjustmentMode,
    ) -> list[OHLCVRecord]:
        records: list[OHLCVRecord] = []
        retrieval_time = datetime.now(UTC)

        for ts, row in df.iterrows():
            timestamp: datetime = ts.to_pydatetime()
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)

            # Convert prices to Decimal with string formatting to preserve exact decimal values
            open_val = Decimal(str(row["open"]))
            high_val = Decimal(str(row["high"]))
            low_val = Decimal(str(row["low"]))
            close_val = Decimal(str(row["close"]))

            adj_c: Decimal | None = None
            if "adj_close" in row and pd.notna(row["adj_close"]):
                adj_c = Decimal(str(row["adj_close"]))
            elif adjustment_mode == PriceAdjustmentMode.SPLIT_AND_DIVIDEND:
                adj_c = close_val

            vol_val = row.get("volume", 0)
            volume = int(round(vol_val)) if pd.notna(vol_val) else 0

            rec = OHLCVRecord(
                instrument_id=instrument_id,
                timestamp=timestamp,
                open=open_val,
                high=high_val,
                low=low_val,
                close=close_val,
                adjusted_close=adj_c,
                volume=volume,
                currency=currency,
                provider=self.name,
                interval=interval,
                adjustment_mode=adjustment_mode,
                retrieval_time=retrieval_time,
            )
            records.append(rec)

        return records

    def _normalize_exception(self, exc: Exception, symbol: str) -> None:
        """Map generic network, HTTP, or library exceptions into domain exceptions."""
        exc_str = str(exc).lower()

        if "too many requests" in exc_str or "rate limit" in exc_str or "429" in exc_str:
            raise ProviderRateLimitError(
                f"Yahoo Finance rate limit exceeded requesting '{symbol}'",
                provider=self.name,
                retry_after=60.0,
                details={"symbol": symbol, "original_error": str(exc)},
            ) from exc

        if "timeout" in exc_str or "timed out" in exc_str:
            raise ProviderTimeoutError(
                f"Yahoo Finance request timed out for '{symbol}'",
                provider=self.name,
                details={"symbol": symbol, "original_error": str(exc)},
            ) from exc

        if (
            "connection" in exc_str
            or "nodename nor servname provided" in exc_str
            or "failed to establish a new connection" in exc_str
            or "dns" in exc_str
        ):
            raise ProviderConnectionError(
                f"Connection failure communicating with Yahoo Finance for '{symbol}'",
                provider=self.name,
                details={"symbol": symbol, "original_error": str(exc)},
            ) from exc

        if "not found" in exc_str or "404" in exc_str or "delisted" in exc_str:
            raise InstrumentNotFoundError(
                f"Symbol '{symbol}' not found on Yahoo Finance",
                provider=self.name,
                details={"symbol": symbol, "original_error": str(exc)},
            ) from exc

        # Default fallback
        raise ProviderConnectionError(
            f"Unexpected provider error for '{symbol}': {exc}",
            provider=self.name,
            details={"symbol": symbol, "original_error": str(exc)},
        ) from exc
