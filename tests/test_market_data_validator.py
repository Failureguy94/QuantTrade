"""Unit tests for MarketDataValidator and data integrity validation rules."""

import uuid
import zoneinfo
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.market_data.exceptions import DataValidationError
from app.market_data.types import OHLCVRecord, PriceAdjustmentMode
from app.market_data.validator import MarketDataValidator


def make_valid_candle(
    instrument_id: uuid.UUID | None = None,
    timestamp: datetime | None = None,
    open_price: str = "100.00",
    high_price: str = "105.00",
    low_price: str = "98.00",
    close_price: str = "102.50",
    adjusted_close: str | None = "102.50",
    volume: int = 1500000,
    currency: str = "USD",
    interval: str = "1d",
    adjustment_mode: PriceAdjustmentMode = PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
) -> OHLCVRecord:
    return OHLCVRecord(
        instrument_id=instrument_id or uuid.uuid4(),
        timestamp=timestamp or datetime(2026, 11, 2, 0, 0, tzinfo=UTC),
        open=Decimal(open_price),
        high=Decimal(high_price),
        low=Decimal(low_price),
        close=Decimal(close_price),
        adjusted_close=Decimal(adjusted_close) if adjusted_close else None,
        volume=volume,
        currency=currency,
        provider="YAHOO",
        interval=interval,
        adjustment_mode=adjustment_mode,
        retrieval_time=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
    )


def test_valid_candle_passes():
    candle = make_valid_candle()
    errors = MarketDataValidator.validate_single_record(candle)
    assert errors == []


def test_reject_timezone_naive():
    # OHLCVRecord post_init blocks naive timestamps
    with pytest.raises(ValueError, match="timezone-aware"):
        make_valid_candle(timestamp=datetime(2026, 11, 2, 0, 0))


def test_reject_non_utc_timezone():
    # Non-UTC timezone should be flagged
    est = zoneinfo.ZoneInfo("America/New_York")
    candle = make_valid_candle(timestamp=datetime(2026, 11, 2, 9, 30, tzinfo=est))
    errors = MarketDataValidator.validate_single_record(candle)
    assert any("must be UTC" in e for e in errors)


def test_reject_negative_or_zero_prices():
    # Zero open
    c1 = make_valid_candle(open_price="0.00")
    errs1 = MarketDataValidator.validate_single_record(c1)
    assert any("Non-positive open price" in e for e in errs1)

    # Negative close
    c2 = make_valid_candle(close_price="-5.00")
    errs2 = MarketDataValidator.validate_single_record(c2)
    assert any("Non-positive close price" in e for e in errs2)

    # Negative adjusted_close
    c3 = make_valid_candle(adjusted_close="-1.00")
    errs3 = MarketDataValidator.validate_single_record(c3)
    assert any("Non-positive adjusted_close" in e for e in errs3)


def test_reject_negative_volume():
    c = make_valid_candle(volume=-100)
    errors = MarketDataValidator.validate_single_record(c)
    assert any("Negative volume" in e for e in errors)


def test_zero_volume_is_permitted():
    # Zero volume is valid for holidays or illiquid instruments
    c = make_valid_candle(volume=0)
    errors = MarketDataValidator.validate_single_record(c)
    assert errors == []


def test_reject_high_less_than_low():
    c = make_valid_candle(high_price="95.00", low_price="105.00")
    errors = MarketDataValidator.validate_single_record(c)
    assert any("High (95.00) < Low (105.00)" in e for e in errors)


def test_reject_open_outside_high_low_range():
    # Open > High
    c1 = make_valid_candle(open_price="110.00", high_price="105.00", low_price="95.00")
    errors1 = MarketDataValidator.validate_single_record(c1)
    assert any("Open (110.00) > High (105.00)" in e for e in errors1)

    # Open < Low
    c2 = make_valid_candle(open_price="90.00", high_price="105.00", low_price="95.00")
    errors2 = MarketDataValidator.validate_single_record(c2)
    assert any("Open (90.00) < Low (95.00)" in e for e in errors2)


def test_reject_close_outside_high_low_range():
    # Close > High
    c1 = make_valid_candle(close_price="108.00", high_price="105.00", low_price="95.00")
    errors1 = MarketDataValidator.validate_single_record(c1)
    assert any("Close (108.00) > High (105.00)" in e for e in errors1)

    # Close < Low
    c2 = make_valid_candle(close_price="92.00", high_price="105.00", low_price="95.00")
    errors2 = MarketDataValidator.validate_single_record(c2)
    assert any("Close (92.00) < Low (95.00)" in e for e in errors2)


def test_currency_mismatch_detection():
    c = make_valid_candle(currency="USD")
    errors = MarketDataValidator.validate_single_record(c, expected_currency="INR")
    assert any("Currency mismatch" in e for e in errors)


def test_series_duplicate_timestamp_rejection():
    inst_id = uuid.uuid4()
    ts = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    c1 = make_valid_candle(instrument_id=inst_id, timestamp=ts)
    c2 = make_valid_candle(instrument_id=inst_id, timestamp=ts)

    report = MarketDataValidator.validate_series([c1, c2])
    assert report.is_valid is False
    assert any("Duplicate timestamp" in e for e in report.errors)


def test_series_unsorted_timestamps_rejection():
    inst_id = uuid.uuid4()
    c1 = make_valid_candle(instrument_id=inst_id, timestamp=datetime(2026, 11, 3, 0, 0, tzinfo=UTC))
    c2 = make_valid_candle(instrument_id=inst_id, timestamp=datetime(2026, 11, 2, 0, 0, tzinfo=UTC))

    report = MarketDataValidator.validate_series([c1, c2])
    assert report.is_valid is False
    assert any("Unsorted timestamps" in e for e in report.errors)


def test_series_mixed_adjustment_mode_rejection():
    inst_id = uuid.uuid4()
    c1 = make_valid_candle(
        instrument_id=inst_id,
        timestamp=datetime(2026, 11, 2, 0, 0, tzinfo=UTC),
        adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
    )
    c2 = make_valid_candle(
        instrument_id=inst_id,
        timestamp=datetime(2026, 11, 3, 0, 0, tzinfo=UTC),
        adjustment_mode=PriceAdjustmentMode.RAW,
    )

    report = MarketDataValidator.validate_series([c1, c2])
    assert report.is_valid is False
    assert any("Never silently mix adjusted and unadjusted" in e for e in report.errors)


def test_series_suspicious_gap_detection():
    inst_id = uuid.uuid4()
    # Create two bars separated by 3 weeks of active trading days
    c1 = make_valid_candle(
        instrument_id=inst_id,
        timestamp=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
    )
    c2 = make_valid_candle(
        instrument_id=inst_id,
        timestamp=datetime(2026, 10, 30, 0, 0, tzinfo=UTC),
    )

    report = MarketDataValidator.validate_series([c1, c2], exchange_mic="XNAS")
    assert report.is_valid is True  # Warning, not hard error
    assert len(report.suspicious_gaps) > 0
    assert any("Suspicious gap" in w for w in report.warnings)


def test_normalize_and_validate_sorts_and_raises():
    inst_id = uuid.uuid4()
    c1 = make_valid_candle(instrument_id=inst_id, timestamp=datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    c2 = make_valid_candle(instrument_id=inst_id, timestamp=datetime(2026, 11, 2, 0, 0, tzinfo=UTC))
    c3 = make_valid_candle(instrument_id=inst_id, timestamp=datetime(2026, 11, 3, 0, 0, tzinfo=UTC))

    cleaned = MarketDataValidator.normalize_and_validate([c1, c2, c3])
    assert [c.timestamp.day for c in cleaned] == [2, 3, 4]

    # Failing candle raises DataValidationError
    bad_candle = make_valid_candle(
        instrument_id=inst_id,
        timestamp=datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
        high_price="50.00",
        low_price="100.00",
    )
    with pytest.raises(DataValidationError, match="Market data validation failed"):
        MarketDataValidator.normalize_and_validate([c1, bad_candle])
