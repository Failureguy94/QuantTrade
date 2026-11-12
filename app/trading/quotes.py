"""Reference prices for paper fills and portfolio valuation.

A quote is the close of the most recent daily bar the provider reports
(unadjusted, i.e. the price that actually traded). Quotes are held in memory for
a short TTL and are deliberately **not** written to the candle store, so a bar
that is still forming can never leak into backtest data. If the provider cannot
be reached, the newest stored daily candle is used instead, provided it is recent
enough.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.market_data.base import MarketDataProvider
from app.market_data.exceptions import MarketDataError
from app.market_data.service import MarketDataService
from app.market_data.types import PriceAdjustmentMode
from app.models.market_data import MarketDataCandle

logger = logging.getLogger(__name__)

PROVIDER_LOOKBACK_DAYS = 10
CANDLE_PRICE_QUANTUM = Decimal("0.000001")  # NUMERIC(20, 6)


class QuoteUnavailableError(Exception):
    """No sufficiently recent price exists for the instrument."""


@dataclass(frozen=True)
class Quote:
    instrument_id: uuid.UUID
    price: Decimal
    currency: str
    as_of: datetime
    source: str


class QuoteService:
    _cache: ClassVar[dict[uuid.UUID, tuple[float, Quote]]] = {}

    def __init__(
        self,
        db: AsyncSession,
        provider: MarketDataProvider | None = None,
        *,
        ttl_seconds: int | None = None,
        max_age_days: int | None = None,
    ) -> None:
        self.db = db
        self.market_data = MarketDataService(db, provider=provider)
        self.provider = provider or self.market_data.get_provider()
        self.ttl_seconds = settings.quote_cache_ttl_seconds if ttl_seconds is None else ttl_seconds
        self.max_age = timedelta(
            days=settings.quote_max_age_days if max_age_days is None else max_age_days
        )

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    async def get_quote(self, instrument_id: uuid.UUID) -> Quote:
        """Return a usable quote or raise ``QuoteUnavailableError``."""
        now = datetime.now(UTC)
        cached = self._cache.get(instrument_id)
        if cached is not None and time.monotonic() - cached[0] < self.ttl_seconds:
            return cached[1]

        quote = await self._from_provider(instrument_id, now)
        if quote is None:
            quote = await self._from_store(instrument_id)
        if quote is None:
            raise QuoteUnavailableError("No price is available for this instrument")
        if now - quote.as_of > self.max_age:
            raise QuoteUnavailableError(
                f"Latest available price is from {quote.as_of.date()}, older than the "
                f"{self.max_age.days}-day limit"
            )
        if quote.source != "STORE":
            self._cache[instrument_id] = (time.monotonic(), quote)
        return quote

    async def _from_provider(self, instrument_id: uuid.UUID, now: datetime) -> Quote | None:
        try:
            instrument = await self.market_data.get_instrument_with_relations(instrument_id)
            symbol = self.market_data.resolve_provider_symbol(instrument, self.provider)
            bars = await self.provider.fetch_historical_bars(
                provider_symbol=symbol,
                start=now - timedelta(days=PROVIDER_LOOKBACK_DAYS),
                end=now + timedelta(days=1),
                interval="1d",
                adjustment_mode=PriceAdjustmentMode.RAW,
                instrument_id=instrument.id,
                currency=instrument.currency_code,
            )
        except MarketDataError as exc:
            logger.warning("Quote fetch failed for %s: %s", instrument_id, exc)
            return None
        except Exception:  # noqa: BLE001 - a provider bug must not take trading down
            logger.exception("Unexpected provider error fetching quote for %s", instrument_id)
            return None

        usable = [bar for bar in bars if bar.close.is_finite() and bar.close > 0]
        if not usable:
            return None
        latest = max(usable, key=lambda bar: bar.timestamp)
        return Quote(
            instrument_id=instrument_id,
            # Same precision as the candle store, so a provider quote and a stored
            # candle for the same bar are the same number.
            price=latest.close.quantize(CANDLE_PRICE_QUANTUM),
            currency=instrument.currency_code,
            as_of=latest.timestamp,
            source=self.provider.name,
        )

    async def _from_store(self, instrument_id: uuid.UUID) -> Quote | None:
        prefer_raw = case(
            (MarketDataCandle.adjustment_mode == PriceAdjustmentMode.RAW.value, 0), else_=1
        )
        stmt = (
            select(MarketDataCandle)
            .where(
                MarketDataCandle.instrument_id == instrument_id,
                MarketDataCandle.interval == "1d",
            )
            .order_by(MarketDataCandle.timestamp.desc(), prefer_raw)
            .limit(1)
        )
        candle = (await self.db.execute(stmt)).scalar_one_or_none()
        if candle is None:
            return None
        return Quote(
            instrument_id=instrument_id,
            price=candle.close,
            currency=candle.currency,
            as_of=candle.timestamp,
            source="STORE",
        )
