"""Database persistence, precision, check constraints, repository, and seed tests."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_data.fingerprint import compute_dataset_fingerprint
from app.market_data.repository import SQLMarketDataRepository
from app.market_data.seed import seed_market_reference_data
from app.market_data.types import OHLCVRecord, PriceAdjustmentMode
from app.models.identity import Exchange, Instrument
from app.models.market_data import MarketDataCandle


@pytest.fixture
async def setup_instrument(db_session: AsyncSession) -> Instrument:
    exchange = Exchange(
        mic="XNAS",
        name="NASDAQ Stock Market",
        country_code="US",
        timezone="America/New_York",
        currency_code="USD",
        calendar_code="XNAS",
        is_active=True,
    )
    db_session.add(exchange)
    await db_session.flush()

    instrument = Instrument(
        exchange_id=exchange.id,
        symbol="AAPL",
        name="Apple Inc.",
        asset_type="EQUITY",
        currency_code="USD",
        isin="US0378331005",
        is_active=True,
    )
    db_session.add(instrument)
    await db_session.flush()
    return instrument


@pytest.mark.asyncio
async def test_candle_persistence_and_precision(
    db_session: AsyncSession, setup_instrument: Instrument
):
    inst = setup_instrument
    ts = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    open_p = Decimal("225.123456")
    high_p = Decimal("228.987654")
    low_p = Decimal("224.000001")
    close_p = Decimal("227.555555")
    vol = 4876543210  # > 2 billion, tests BigInteger

    candle = MarketDataCandle(
        instrument_id=inst.id,
        timestamp=ts,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        adjusted_close=close_p,
        volume=vol,
        currency="USD",
        interval="1d",
        adjustment_mode="SPLIT_AND_DIVIDEND",
        provider="YAHOO",
        retrieval_time=datetime.now(UTC),
    )
    db_session.add(candle)
    await db_session.flush()

    assert candle.id is not None

    stmt = select(MarketDataCandle).where(MarketDataCandle.id == candle.id)
    fetched = (await db_session.execute(stmt)).scalar_one()

    # Exact decimal preservation up to 6 places
    assert isinstance(fetched.open, Decimal)
    assert fetched.open == open_p
    assert str(fetched.open) == "225.123456"
    assert fetched.high == high_p
    assert fetched.low == low_p
    assert fetched.close == close_p
    # BigInteger volume preservation
    assert fetched.volume == vol
    assert isinstance(fetched.volume, int)


@pytest.mark.asyncio
async def test_duplicate_candle_rejection(
    db_session: AsyncSession, setup_instrument: Instrument
):
    inst = setup_instrument
    ts = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)

    c1 = MarketDataCandle(
        instrument_id=inst.id,
        timestamp=ts,
        open=Decimal("100.00"),
        high=Decimal("105.00"),
        low=Decimal("99.00"),
        close=Decimal("103.00"),
        volume=100000,
        currency="USD",
        interval="1d",
        adjustment_mode="SPLIT_AND_DIVIDEND",
        provider="YAHOO",
        retrieval_time=datetime.now(UTC),
    )
    db_session.add(c1)
    await db_session.flush()

    # Attempt inserting identical duplicate (same instrument, interval, adjustment_mode, timestamp)
    c2 = MarketDataCandle(
        instrument_id=inst.id,
        timestamp=ts,
        open=Decimal("101.00"),
        high=Decimal("106.00"),
        low=Decimal("100.00"),
        close=Decimal("104.00"),
        volume=200000,
        currency="USD",
        interval="1d",
        adjustment_mode="SPLIT_AND_DIVIDEND",
        provider="YAHOO",
        retrieval_time=datetime.now(UTC),
    )
    db_session.add(c2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_database_check_constraint_high_ge_low(
    db_session: AsyncSession, setup_instrument: Instrument
):
    inst = setup_instrument
    c = MarketDataCandle(
        instrument_id=inst.id,
        timestamp=datetime(2026, 11, 2, 0, 0, tzinfo=UTC),
        open=Decimal("100.00"),
        high=Decimal("95.00"),   # High < Low
        low=Decimal("105.00"),
        close=Decimal("100.00"),
        volume=1000,
        currency="USD",
        interval="1d",
        adjustment_mode="SPLIT_AND_DIVIDEND",
        provider="YAHOO",
        retrieval_time=datetime.now(UTC),
    )
    db_session.add(c)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_database_check_constraint_negative_volume(
    db_session: AsyncSession, setup_instrument: Instrument
):
    inst = setup_instrument
    c = MarketDataCandle(
        instrument_id=inst.id,
        timestamp=datetime(2026, 11, 2, 0, 0, tzinfo=UTC),
        open=Decimal("100.00"),
        high=Decimal("105.00"),
        low=Decimal("95.00"),
        close=Decimal("100.00"),
        volume=-50,  # Negative volume
        currency="USD",
        interval="1d",
        adjustment_mode="SPLIT_AND_DIVIDEND",
        provider="YAHOO",
        retrieval_time=datetime.now(UTC),
    )
    db_session.add(c)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_repository_save_and_upsert(
    db_session: AsyncSession, setup_instrument: Instrument
):
    inst = setup_instrument
    repo = SQLMarketDataRepository(db_session)
    ts = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)

    rec1 = OHLCVRecord(
        instrument_id=inst.id,
        timestamp=ts,
        open=Decimal("150.00"),
        high=Decimal("155.00"),
        low=Decimal("149.00"),
        close=Decimal("153.00"),
        adjusted_close=Decimal("153.00"),
        volume=500000,
        currency="USD",
        provider="YAHOO",
        interval="1d",
        adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        retrieval_time=datetime.now(UTC),
    )

    # Initial save
    saved_count = await repo.save_candles([rec1], upsert=True)
    await db_session.flush()
    assert saved_count == 1

    # Query back
    candles = await repo.get_candles(instrument_id=inst.id, interval="1d")
    assert len(candles) == 1
    assert candles[0].close == Decimal("153.000000")

    # Upsert with updated close and volume
    rec1_updated = OHLCVRecord(
        instrument_id=inst.id,
        timestamp=ts,
        open=Decimal("150.00"),
        high=Decimal("155.00"),
        low=Decimal("149.00"),
        close=Decimal("154.50"),  # updated close
        adjusted_close=Decimal("154.50"),
        volume=600000,           # updated volume
        currency="USD",
        provider="YAHOO",
        interval="1d",
        adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
        retrieval_time=datetime.now(UTC),
    )
    await repo.save_candles([rec1_updated], upsert=True)
    await db_session.flush()

    candles_after_upsert = await repo.get_candles(instrument_id=inst.id, interval="1d")
    assert len(candles_after_upsert) == 1
    assert candles_after_upsert[0].close == Decimal("154.500000")
    assert candles_after_upsert[0].volume == 600000


def test_reproducibility_fingerprint_deterministic():
    inst_id = uuid.uuid4()
    ts1 = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    ts2 = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)

    b1 = OHLCVRecord(
        instrument_id=inst_id,
        timestamp=ts1,
        open=Decimal("100.00"),
        high=Decimal("105.00"),
        low=Decimal("98.00"),
        close=Decimal("102.00"),
        volume=10000,
        currency="USD",
        provider="YAHOO",
        interval="1d",
    )
    b2 = OHLCVRecord(
        instrument_id=inst_id,
        timestamp=ts2,
        open=Decimal("102.00"),
        high=Decimal("106.00"),
        low=Decimal("101.00"),
        close=Decimal("105.00"),
        volume=12000,
        currency="USD",
        provider="YAHOO",
        interval="1d",
    )

    fp1 = compute_dataset_fingerprint([b1, b2])
    fp2 = compute_dataset_fingerprint([b2, b1])  # Order permutation gives identical hash
    assert fp1 == fp2
    assert len(fp1) == 64

    # Changing any single price modifies the fingerprint completely
    b2_altered = OHLCVRecord(
        instrument_id=inst_id,
        timestamp=ts2,
        open=Decimal("102.01"),  # 1 cent change
        high=Decimal("106.00"),
        low=Decimal("101.00"),
        close=Decimal("105.00"),
        volume=12000,
        currency="USD",
        provider="YAHOO",
        interval="1d",
    )
    fp_altered = compute_dataset_fingerprint([b1, b2_altered])
    assert fp_altered != fp1


@pytest.mark.asyncio
async def test_seed_market_reference_data_idempotency(db_session: AsyncSession):
    # Run seed function
    counts1 = await seed_market_reference_data(db_session)
    await db_session.flush()

    assert counts1["exchanges"] == 4  # XNSE, XBOM, XNAS, XNYS
    assert counts1["instruments"] >= 6
    assert counts1["provider_symbols"] >= 6

    # Running a second time should not duplicate
    counts2 = await seed_market_reference_data(db_session)
    await db_session.flush()

    assert counts2["exchanges"] == 0
    assert counts2["instruments"] == 0
    assert counts2["provider_symbols"] == 0
