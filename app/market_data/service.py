"""Market data service orchestrating providers, validation, caching, and repository."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.market_data.base import MarketDataProvider
from app.market_data.exceptions import (
    DataValidationError,
    InstrumentNotFoundError,
    InvalidIntervalError,
    MarketDataError,
)
from app.market_data.fingerprint import compute_dataset_fingerprint
from app.market_data.repository import SQLMarketDataRepository
from app.market_data.types import (
    DataInterval,
    OHLCVRecord,
    PriceAdjustmentMode,
    ProviderCapabilities,
)
from app.market_data.validator import MarketDataValidator
from app.market_data.yahoo import YahooFinanceProvider
from app.models.identity import Instrument

logger = logging.getLogger(__name__)

# How far short of the requested range cached bars may fall before the range is
# refetched. Wide enough for weekends and holiday runs at each bar size.
_COVERAGE_TOLERANCE = {
    "1d": timedelta(days=5),
    "5d": timedelta(days=10),
    "1wk": timedelta(days=10),
    "1mo": timedelta(days=35),
}


class MarketDataService:
    """Core market data engine coordinator."""

    def __init__(self, db: AsyncSession, provider: MarketDataProvider | None = None) -> None:
        self.db = db
        self.repository = SQLMarketDataRepository(db)
        self._providers: dict[str, MarketDataProvider] = {}
        # The provider used when a request does not name one.
        default_provider = provider or YahooFinanceProvider()
        self.default_provider_name = default_provider.name.upper()
        self.register_provider(default_provider)

    def register_provider(self, provider: MarketDataProvider) -> None:
        self._providers[provider.name.upper()] = provider

    def get_provider(self, name: str | None = None) -> MarketDataProvider:
        """The named provider, or the configured default when no name is given."""
        name = name or self.default_provider_name
        provider = self._providers.get(name.upper())
        if not provider:
            registered = list(self._providers.keys())
            raise MarketDataError(
                f"Market data provider '{name}' is not registered. Registered: {registered}"
            )
        return provider

    def list_providers(self) -> list[ProviderCapabilities]:
        return [p.capabilities for p in self._providers.values()]

    async def get_instrument_with_relations(self, instrument_id: uuid.UUID) -> Instrument:
        stmt = (
            select(Instrument)
            .where(Instrument.id == instrument_id)
            .options(
                selectinload(Instrument.exchange),
                selectinload(Instrument.provider_symbols),
            )
        )
        result = await self.db.execute(stmt)
        instrument = result.scalar_one_or_none()
        if not instrument:
            raise InstrumentNotFoundError(
                f"Instrument with ID '{instrument_id}' was not found",
                details={"instrument_id": str(instrument_id)},
            )
        return instrument

    def resolve_provider_symbol(
        self,
        instrument: Instrument,
        provider: MarketDataProvider,
    ) -> str:
        """Resolve native ticker using explicit database mapping or algorithmic resolution."""
        # Check explicit database mapping
        for ps in instrument.provider_symbols:
            if ps.provider.upper() == provider.name.upper() and ps.is_primary:
                return ps.provider_symbol

        # Fallback to provider symbol resolver
        resolved = provider.resolve_symbol(instrument.exchange.mic, instrument.symbol)
        if not resolved:
            raise InstrumentNotFoundError(
                f"Could not resolve provider symbol for {instrument.symbol} "
                f"on {instrument.exchange.mic} for {provider.name}"
            )
        return resolved

    async def get_historical_market_data(
        self,
        instrument_id: uuid.UUID,
        interval: str = "1d",
        adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        start: datetime | None = None,
        end: datetime | None = None,
        provider_name: str | None = None,
        force_fetch: bool = False,
        limit: int = 1000,
    ) -> tuple[Instrument, list[OHLCVRecord], str, list[str]]:
        """
        Retrieve historical candles with validation, persistence,
        and cryptographic snapshot fingerprinting.
        Returns: (instrument, records, data_fingerprint, warnings)
        """
        instrument = await self.get_instrument_with_relations(instrument_id)
        provider = self.get_provider(provider_name)

        if not DataInterval.is_valid(interval):
            raise InvalidIntervalError(
                interval=interval,
                supported_intervals=DataInterval.get_supported_list(),
                provider=provider.name,
            )

        now = datetime.now(UTC)
        if end is None:
            end = now
        elif end.tzinfo is None:
            end = end.replace(tzinfo=UTC)

        if start is None:
            # Default to 1 year for daily, 7 days for intraday
            if interval in {"1m", "2m", "5m", "15m", "30m", "1h"}:
                start = end - timedelta(days=7)
            else:
                start = end - timedelta(days=365)
        elif start.tzinfo is None:
            start = start.replace(tzinfo=UTC)

        if start > end:
            raise DataValidationError(f"Start date ({start}) cannot be after end date ({end})")

        warnings: list[str] = []

        # 1. Query existing candles in database
        existing_candles = []
        if not force_fetch:
            existing_candles = await self.repository.get_candles(
                instrument_id=instrument.id,
                interval=interval,
                adjustment_mode=adjustment_mode,
                start=start,
                end=end,
                limit=limit,
            )

        # 2. If no data exists or force_fetch is requested, fetch from upstream provider
        if not existing_candles or force_fetch:
            provider_symbol = self.resolve_provider_symbol(instrument, provider)
            logger.info(
                "Fetching historical market data from provider %s for symbol %s (range: %s to %s)",
                provider.name,
                provider_symbol,
                start,
                end,
            )
            raw_records = await provider.fetch_historical_bars(
                provider_symbol=provider_symbol,
                start=start,
                end=end,
                interval=interval,
                adjustment_mode=adjustment_mode,
                instrument_id=instrument.id,
                currency=instrument.currency_code,
            )

            # Validate before saving
            report = MarketDataValidator.validate_series(
                raw_records,
                expected_currency=instrument.currency_code,
                exchange_mic=instrument.exchange.mic,
            )
            report.raise_for_errors()
            warnings.extend(report.warnings)

            # Persist clean records
            if raw_records:
                await self.repository.save_candles(raw_records, upsert=True)
                await self.db.flush()

            # Retrieve refreshed data from repository to ensure consistency
            candles = await self.repository.get_candles(
                instrument_id=instrument.id,
                interval=interval,
                adjustment_mode=adjustment_mode,
                start=start,
                end=end,
                limit=limit,
            )
        else:
            candles = existing_candles

        fingerprint = compute_dataset_fingerprint(candles)
        return instrument, candles, fingerprint, warnings

    async def get_covering_market_data(
        self,
        instrument_id: uuid.UUID,
        *,
        start: datetime,
        end: datetime,
        interval: str = "1d",
        adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        provider_name: str | None = None,
        limit: int = 1000,
    ) -> tuple[Instrument, list[OHLCVRecord], str, list[str]]:
        """Historical bars that are checked to span ``start``..``end``.

        ``get_historical_market_data`` serves whatever is cached for the range, even
        when the cache holds only part of it. Analytics need the whole range, so if
        the cached bars begin late or end early the range is fetched again from the
        provider. If the provider's data still falls short (for example the
        instrument listed after ``start``), a warning says what is actually covered.
        """
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)

        cached = await self.repository.get_candles(
            instrument_id=instrument_id,
            interval=interval,
            adjustment_mode=adjustment_mode,
            start=start,
            end=end,
            limit=limit,
        )
        refetch = bool(self._coverage_gaps(cached, start, end, interval))
        if refetch and cached:
            logger.info(
                "Cached bars for %s cover only part of %s..%s; refetching",
                instrument_id,
                start.date(),
                end.date(),
            )
        instrument, bars, fingerprint, warnings = await self.get_historical_market_data(
            instrument_id=instrument_id,
            interval=interval,
            adjustment_mode=adjustment_mode,
            start=start,
            end=end,
            provider_name=provider_name,
            force_fetch=refetch,
            limit=limit,
        )
        if refetch:
            warnings = [*warnings, *self._coverage_gaps(bars, start, end, interval)]
        return instrument, bars, fingerprint, warnings

    @staticmethod
    def _coverage_gaps(
        bars: list[OHLCVRecord], start: datetime, end: datetime, interval: str
    ) -> list[str]:
        if not bars:
            return ["No bars are available for the requested range"]
        tolerance = _COVERAGE_TOLERANCE.get(interval, timedelta(days=5))
        last_expected = min(end, datetime.now(UTC))
        gaps: list[str] = []
        if bars[0].timestamp - start > tolerance:
            gaps.append(
                f"Data begins on {bars[0].timestamp.date()}, after the requested start "
                f"{start.date()}"
            )
        if last_expected - bars[-1].timestamp > tolerance:
            gaps.append(
                f"Data ends on {bars[-1].timestamp.date()}, before the requested end "
                f"{last_expected.date()}"
            )
        return gaps
