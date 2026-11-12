"""Exchange calendar and trading session management.

Supports:
- India: National Stock Exchange (XNSE), BSE (XBOM)
- United States: NASDAQ (XNAS), New York Stock Exchange (XNYS)

## Known limitations

**US calendars** use a fixed list of federal holidays with standard
weekend-observance rules (Saturday → Friday, Sunday → Monday) plus
heuristic day-of-month ranges for floating holidays (MLK Day, Presidents'
Day, Memorial Day, Labor Day, Thanksgiving).  Early-close sessions (e.g.
day before Thanksgiving, Christmas Eve) are not modelled.  Daylight-saving
transitions do not affect trading-day detection (they affect session
open/close times, not the existence of a session).

**India calendars** cover only six fixed national holidays.  NSE and BSE
each observe approximately 14–18 holidays per year including several
floating religious observances (Diwali/Muhurat Trading, Holi, Eid ul-Fitr,
Eid ul-Adha, Dussehra, etc.) whose dates shift annually.  These floating
holidays are **not modelled here**.  As a consequence, the
``detect_missing_sessions`` output will report some genuine exchange
holidays as "missing sessions".

For production-grade calendars consider the ``exchange_calendars`` package
(https://github.com/gerrymanoim/exchange_calendars) which maintains
accurate, authoritative schedules for XNSE, XBOM, XNYS and XNAS.
"""

import zoneinfo
from datetime import date, datetime, timedelta
from typing import ClassVar


class ExchangeCalendar:
    """Base calendar modeling exchange trading schedules and holidays."""

    MIC: ClassVar[str] = ""
    NAME: ClassVar[str] = ""
    COUNTRY: ClassVar[str] = ""
    TIMEZONE: ClassVar[str] = "UTC"
    CURRENCY: ClassVar[str] = "USD"

    # Known fixed or recurring holidays (month, day)
    FIXED_HOLIDAYS: ClassVar[set[tuple[int, int]]] = set()

    def __init__(self) -> None:
        self.tz = zoneinfo.ZoneInfo(self.TIMEZONE)

    def is_weekend(self, dt: date) -> bool:
        """Returns True if day is Saturday (5) or Sunday (6)."""
        return dt.weekday() >= 5

    def is_holiday(self, dt: date) -> bool:
        """Check if date is an exchange holiday."""
        if (dt.month, dt.day) in self.FIXED_HOLIDAYS:
            return True
        return False

    def is_trading_day(self, dt: date | datetime) -> bool:
        """Return True if the date is a regular scheduled trading session."""
        d = dt.date() if isinstance(dt, datetime) else dt
        if self.is_weekend(d):
            return False
        if self.is_holiday(d):
            return False
        return True

    def get_expected_sessions(self, start: date, end: date) -> list[date]:
        """Generate all expected trading dates between start and end inclusive."""
        sessions: list[date] = []
        cur = start
        while cur <= end:
            if self.is_trading_day(cur):
                sessions.append(cur)
            cur += timedelta(days=1)
        return sessions

    def detect_missing_sessions(
        self, actual_dates: set[date], start: date, end: date
    ) -> list[date]:
        """Identify expected trading days that have no data recorded."""
        expected = self.get_expected_sessions(start, end)
        return [session for session in expected if session not in actual_dates]


def _observed_weekday(holiday: date) -> date:
    """Return the weekday on which a fixed holiday is observed.

    US markets follow the federal standard:
    - Holiday falls on Saturday → observed the preceding Friday.
    - Holiday falls on Sunday  → observed the following Monday.
    - Any weekday              → observed on that day.
    """
    wd = holiday.weekday()
    if wd == 5:  # Saturday
        return holiday - timedelta(days=1)
    if wd == 6:  # Sunday
        return holiday + timedelta(days=1)
    return holiday


class USCalendar(ExchangeCalendar):
    """US Exchange Calendar for NYSE (XNYS) and NASDAQ (XNAS).

    Fixed holidays with standard weekend-observance rules plus heuristic
    floating holidays.  Early-close sessions are not modelled.
    """

    TIMEZONE = "America/New_York"
    CURRENCY = "USD"
    COUNTRY = "US"

    # Raw fixed-date holidays (month, day) — before observance adjustment
    _RAW_FIXED: ClassVar[set[tuple[int, int]]] = {
        (1, 1),    # New Year's Day
        (6, 19),   # Juneteenth National Independence Day
        (7, 4),    # Independence Day
        (12, 25),  # Christmas Day
    }

    def is_holiday(self, dt: date) -> bool:
        # Check raw fixed holidays with weekend-observance adjustment.
        # For each fixed holiday use dt.year to construct the holiday date.
        # Exception: New Year's Day on Saturday is observed as Friday Dec 31
        # of the *previous* year, so when dt is in December we must also test
        # whether Jan 1 of the *next* year falls on Saturday.
        for month, day in self._RAW_FIXED:
            raw = date(dt.year, month, day)
            observed = _observed_weekday(raw)
            if dt == observed:
                return True

        # Cross-year edge case: Jan 1 on Saturday → observed Dec 31 of prior year.
        # When evaluating a December date we also check the *next* year's Jan 1.
        if dt.month == 12 and (1, 1) in self._RAW_FIXED:
            next_new_year = date(dt.year + 1, 1, 1)
            if dt == _observed_weekday(next_new_year):
                return True

        month = dt.month
        day = dt.day
        weekday = dt.weekday()

        # MLK Day: 3rd Monday of January
        if month == 1 and weekday == 0 and 15 <= day <= 21:
            return True

        # Presidents' Day: 3rd Monday of February
        if month == 2 and weekday == 0 and 15 <= day <= 21:
            return True

        # Memorial Day: Last Monday of May
        if month == 5 and weekday == 0 and day >= 25:
            return True

        # Labor Day: 1st Monday of September
        if month == 9 and weekday == 0 and day <= 7:
            return True

        # Thanksgiving Day: 4th Thursday of November
        if month == 11 and weekday == 3 and 22 <= day <= 28:
            return True

        return False


class NYSECalendar(USCalendar):
    MIC = "XNYS"
    NAME = "New York Stock Exchange"


class NASDAQCalendar(USCalendar):
    MIC = "XNAS"
    NAME = "NASDAQ Stock Market"


class IndiaCalendar(ExchangeCalendar):
    """Indian Exchange Calendar for NSE (XNSE) and BSE (XBOM).

    **Coverage is intentionally limited.**  Only fixed national holidays are
    modelled.  Floating religious holidays (Diwali/Muhurat Trading, Holi,
    Eid ul-Fitr, Eid ul-Adha, Dussehra, Guru Nanak Jayanti, etc.) vary
    each year and are **not included**.  As a result this calendar will
    under-report holidays and ``detect_missing_sessions`` will produce
    false positives for those dates.

    For accurate Indian market calendars use the ``exchange_calendars``
    package or maintain an explicit holiday list updated annually.
    """

    TIMEZONE = "Asia/Kolkata"
    CURRENCY = "INR"
    COUNTRY = "IN"

    # Fixed national holidays observed by both NSE and BSE every year.
    # Floating religious holidays (Diwali, Holi, Eid, Dussehra, etc.)
    # are NOT included — their dates shift annually.
    FIXED_HOLIDAYS = {
        (1, 26),   # Republic Day
        (4, 14),   # Dr. B.R. Ambedkar Jayanti
        (5, 1),    # Maharashtra Day / May Day
        (8, 15),   # Independence Day
        (10, 2),   # Mahatma Gandhi Jayanti
        (12, 25),  # Christmas Day
    }


class NSECalendar(IndiaCalendar):
    MIC = "XNSE"
    NAME = "National Stock Exchange of India"


class BSECalendar(IndiaCalendar):
    MIC = "XBOM"
    NAME = "BSE Ltd."


_CALENDAR_REGISTRY: dict[str, type[ExchangeCalendar]] = {
    "XNSE": NSECalendar,
    "XBOM": BSECalendar,
    "XNAS": NASDAQCalendar,
    "XNYS": NYSECalendar,
}


def get_calendar(mic_or_code: str) -> ExchangeCalendar:
    """Retrieve calendar instance for a market identifier code (MIC)."""
    normalized = mic_or_code.upper().strip()
    cal_class = _CALENDAR_REGISTRY.get(normalized)
    if not cal_class:
        # Fallback to US Calendar if unknown US exchange, or default ExchangeCalendar
        if normalized.startswith("XU") or normalized in {"NYSE", "NASDAQ"}:
            return NYSECalendar()
        if normalized in {"NSE", "BSE"}:
            return NSECalendar()
        return ExchangeCalendar()
    return cal_class()
