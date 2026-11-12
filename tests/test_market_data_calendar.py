"""Tests for Exchange Trading Calendars (XNSE, XBOM, XNAS, XNYS)."""

from datetime import date

from app.market_data.calendar import (
    BSECalendar,
    ExchangeCalendar,
    NASDAQCalendar,
    NSECalendar,
    NYSECalendar,
    get_calendar,
)


def test_get_calendar_mic_lookup():
    assert isinstance(get_calendar("XNSE"), NSECalendar)
    assert isinstance(get_calendar("xnsE"), NSECalendar)
    assert isinstance(get_calendar("XBOM"), BSECalendar)
    assert isinstance(get_calendar("XNAS"), NASDAQCalendar)
    assert isinstance(get_calendar("XNYS"), NYSECalendar)
    assert isinstance(get_calendar("UNKNOWN_MIC"), ExchangeCalendar)


def test_weekend_detection():
    cal = get_calendar("XNAS")
    # 2026-11-06 is Friday, 2026-11-07 is Saturday, 2026-11-08 is Sunday
    assert cal.is_trading_day(date(2026, 11, 6)) is True
    assert cal.is_trading_day(date(2026, 11, 7)) is False
    assert cal.is_trading_day(date(2026, 11, 8)) is False


def test_us_fixed_and_floating_holidays():
    cal = get_calendar("XNYS")
    # Fixed: New Year's Day (Jan 1), Juneteenth (June 19),
    # Independence Day (July 4), Christmas (Dec 25)
    assert cal.is_trading_day(date(2026, 1, 1)) is False
    assert cal.is_trading_day(date(2026, 6, 19)) is False
    assert cal.is_trading_day(date(2026, 12, 25)) is False

    # Floating: MLK Day (3rd Monday in Jan) -> 2026-01-19
    assert cal.is_trading_day(date(2026, 1, 19)) is False

    # Floating: Thanksgiving (4th Thursday in Nov) -> 2026-11-26
    assert cal.is_trading_day(date(2026, 11, 26)) is False


def test_india_holidays():
    cal = get_calendar("XNSE")
    # Republic Day (Jan 26)
    assert cal.is_trading_day(date(2026, 1, 26)) is False
    # Independence Day (Aug 15)
    assert cal.is_trading_day(date(2026, 8, 15)) is False
    # Gandhi Jayanti (Oct 2)
    assert cal.is_trading_day(date(2026, 10, 2)) is False
    # Maharashtra Day (May 1)
    assert cal.is_trading_day(date(2026, 5, 1)) is False


def test_expected_sessions_and_missing_sessions_detection():
    cal = get_calendar("XNAS")
    start = date(2026, 11, 2)  # Monday
    end = date(2026, 11, 6)    # Friday
    sessions = cal.get_expected_sessions(start, end)
    assert len(sessions) == 5  # Mon, Tue, Wed, Thu, Fri

    # Detect missing trading sessions
    recorded = {
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 5),
        date(2026, 11, 6),
    }  # Missing 2026-11-04 (Wednesday)
    missing = cal.detect_missing_sessions(recorded, start, end)
    assert missing == [date(2026, 11, 4)]
