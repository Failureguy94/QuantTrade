"""Provider-agnostic market data interface and contract."""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from app.market_data.types import OHLCVRecord, PriceAdjustmentMode, ProviderCapabilities


class MarketDataProvider(ABC):
    """
    Provider-agnostic market data provider interface.

    Insulates quantitative research, backtesting, and portfolio risk modules
    from provider-specific network protocols, schemas, rate limits, and symbols.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique uppercase provider identifier (e.g. 'YAHOO', 'POLYGON')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Declare supported intervals, adjustment modes, and service limits."""
        raise NotImplementedError

    def supports_interval(self, interval: str) -> bool:
        """Return True if the interval is supported by this provider."""
        return interval in self.capabilities.supported_intervals

    @abstractmethod
    def resolve_symbol(self, exchange_mic: str, ticker: str) -> str | None:
        """
        Resolve exchange MIC and internal symbol to provider-native notation.
        Examples:
        - XNSE + 'RELIANCE' -> 'RELIANCE.NS'
        - XBOM + 'TCS' -> 'TCS.BO'
        - XNAS + 'AAPL' -> 'AAPL'
        - XNYS + 'SPY' -> 'SPY'
        """
        raise NotImplementedError

    @abstractmethod
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
        """
        Fetch, normalize, and return structured OHLCVRecord instances.
        Must normalize network, rate-limit, and payload errors into MarketDataError exceptions.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_historical_data(
        self,
        provider_symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """
        Backward-compatible DataFrame interface returning UTC-indexed OHLCV data.
        """
        raise NotImplementedError
