"""Market data normalization and rigorous integrity validation engine."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.market_data.calendar import get_calendar
from app.market_data.exceptions import DataValidationError
from app.market_data.types import OHLCVRecord


@dataclass
class ValidationReport:
    """Detailed outcome of validating a series of market data bars."""
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bars_checked: int = 0
    missing_sessions: list[str] = field(default_factory=list)
    suspicious_gaps: list[dict[str, Any]] = field(default_factory=list)

    def raise_for_errors(self) -> None:
        if not self.is_valid:
            raise DataValidationError(
                message=f"Market data validation failed with {len(self.errors)} errors",
                failures=self.errors,
                details={"errors": self.errors, "warnings": self.warnings},
            )


class MarketDataValidator:
    """Enforces strict financial data invariants and integrity constraints."""

    @classmethod
    def validate_single_record(
        cls,
        record: OHLCVRecord,
        expected_currency: str | None = None,
    ) -> list[str]:
        """Validate invariant rules on an individual OHLCV candle."""
        errors: list[str] = []

        # 1. Timezone verification
        if record.timestamp.tzinfo is None:
            errors.append(f"Timestamp {record.timestamp} is timezone-naive; UTC required")
        elif (
            record.timestamp.tzinfo != UTC
            and record.timestamp.utcoffset() != UTC.utcoffset(None)
        ):
            errors.append(f"Timestamp {record.timestamp} timezone must be UTC")

        # 2. Missing/NaN price values
        for price_name, val in [
            ("open", record.open),
            ("high", record.high),
            ("low", record.low),
            ("close", record.close),
        ]:
            if val is None:
                errors.append(f"Missing {price_name} price value at {record.timestamp}")
            elif not isinstance(val, Decimal):
                errors.append(
                    f"{price_name} must be Decimal, got {type(val)} at {record.timestamp}"
                )
            elif val.is_nan() or val.is_infinite():
                errors.append(f"{price_name} is NaN or infinite at {record.timestamp}")
            elif val <= Decimal("0"):
                errors.append(
                    f"Non-positive {price_name} price {val} at {record.timestamp} (must be > 0)"
                )

        # 3. Adjusted close verification if present
        if record.adjusted_close is not None:
            if not isinstance(record.adjusted_close, Decimal):
                errors.append(
                    f"adjusted_close must be Decimal, got {type(record.adjusted_close)} "
                    f"at {record.timestamp}"
                )
            elif record.adjusted_close.is_nan() or record.adjusted_close.is_infinite():
                errors.append(f"adjusted_close is NaN or infinite at {record.timestamp}")
            elif record.adjusted_close <= Decimal("0"):
                errors.append(
                    f"Non-positive adjusted_close {record.adjusted_close} at {record.timestamp}"
                )

        # 4. Volume validation (must be non-negative integer)
        if record.volume is None:
            errors.append(f"Missing volume at {record.timestamp}")
        elif not isinstance(record.volume, int) or isinstance(record.volume, bool):
            errors.append(
                f"Volume must be integer, got {type(record.volume)} at {record.timestamp}"
            )
        elif record.volume < 0:
            errors.append(f"Negative volume {record.volume} at {record.timestamp}")

        # If any price was invalid/missing, skip relational checks
        if any("price" in err for err in errors):
            return errors

        # 5. Invariant: High < Low
        if record.high < record.low:
            errors.append(
                f"High ({record.high}) < Low ({record.low}) invariant violated "
                f"at {record.timestamp}"
            )

        # 6. Invariant: Open within [Low, High]
        if record.open > record.high:
            errors.append(
                f"Open ({record.open}) > High ({record.high}) at {record.timestamp}"
            )
        if record.open < record.low:
            errors.append(
                f"Open ({record.open}) < Low ({record.low}) at {record.timestamp}"
            )

        # 7. Invariant: Close within [Low, High]
        if record.close > record.high:
            errors.append(
                f"Close ({record.close}) > High ({record.high}) at {record.timestamp}"
            )
        if record.close < record.low:
            errors.append(
                f"Close ({record.close}) < Low ({record.low}) at {record.timestamp}"
            )

        # 8. Currency consistency
        if expected_currency and record.currency.upper() != expected_currency.upper():
            errors.append(
                f"Currency mismatch at {record.timestamp}: expected {expected_currency}, "
                f"got {record.currency}"
            )

        return errors

    @classmethod
    def validate_series(
        cls,
        records: list[OHLCVRecord],
        expected_currency: str | None = None,
        exchange_mic: str | None = None,
        max_allowed_consecutive_gap_sessions: int = 5,
    ) -> ValidationReport:
        """
        Validate an entire OHLCV series.
        Enforces chronological sorting, duplicate timestamp rejection,
        uniform interval and adjustment mode, and calendar session continuity.
        """
        if not records:
            return ValidationReport(is_valid=True, bars_checked=0)

        errors: list[str] = []
        warnings: list[str] = []

        first = records[0]
        base_inst_id = first.instrument_id
        base_interval = first.interval
        base_adj_mode = first.adjustment_mode
        base_currency = expected_currency or first.currency

        seen_timestamps: set[datetime] = set()
        prev_timestamp: datetime | None = None

        # Check records
        for i, rec in enumerate(records):
            # Per-candle validation
            candle_errs = cls.validate_single_record(rec, expected_currency=base_currency)
            errors.extend(candle_errs)

            # Uniformity checks across series
            if rec.instrument_id != base_inst_id:
                errors.append(
                    f"Mixed instrument_ids in series at bar {i}: expected {base_inst_id}, "
                    f"got {rec.instrument_id}"
                )
            if rec.interval != base_interval:
                errors.append(
                    f"Mixed intervals in series at bar {i}: expected {base_interval}, "
                    f"got {rec.interval}"
                )
            if rec.adjustment_mode != base_adj_mode:
                errors.append(
                    f"Mixed adjustment modes in series at bar {i}: expected {base_adj_mode}, "
                    f"got {rec.adjustment_mode}. Never silently mix adjusted and unadjusted "
                    "OHLCV series."
                )

            # Duplicate timestamp check
            if rec.timestamp in seen_timestamps:
                errors.append(f"Duplicate timestamp encountered in series: {rec.timestamp}")
            seen_timestamps.add(rec.timestamp)

            # Monotonic ordering check
            if prev_timestamp is not None and rec.timestamp <= prev_timestamp:
                errors.append(
                    f"Unsorted timestamps: {rec.timestamp} occurs at or before preceding "
                    f"timestamp {prev_timestamp}"
                )
            prev_timestamp = rec.timestamp

        # Calendar and session gap detection for daily intervals
        missing_sessions_list: list[str] = []
        suspicious_gaps: list[dict[str, Any]] = []

        if exchange_mic and base_interval in {"1d", "d", "1day"}:
            calendar = get_calendar(exchange_mic)
            start_date = records[0].timestamp.date()
            end_date = records[-1].timestamp.date()

            actual_dates = {rec.timestamp.date() for rec in records}
            missing_dates = calendar.detect_missing_sessions(actual_dates, start_date, end_date)

            if missing_dates:
                missing_sessions_list = [d.isoformat() for d in missing_dates]

                # Group consecutive missing trading sessions into gaps
                gap_start: date | None = None
                consecutive_count = 0

                expected_sessions = calendar.get_expected_sessions(start_date, end_date)
                for session_date in expected_sessions:
                    if session_date in missing_dates:
                        consecutive_count += 1
                        if gap_start is None:
                            gap_start = session_date
                    else:
                        if consecutive_count >= max_allowed_consecutive_gap_sessions:
                            suspicious_gaps.append({
                                "start": gap_start.isoformat() if gap_start else "",
                                "end": (session_date).isoformat(),
                                "consecutive_missing_sessions": consecutive_count,
                            })
                            warnings.append(
                                f"Suspicious gap of {consecutive_count} consecutive trading "
                                f"sessions between {gap_start} and {session_date}"
                            )
                        gap_start = None
                        consecutive_count = 0

                if consecutive_count >= max_allowed_consecutive_gap_sessions:
                    suspicious_gaps.append({
                        "start": gap_start.isoformat() if gap_start else "",
                        "end": end_date.isoformat(),
                        "consecutive_missing_sessions": consecutive_count,
                    })
                    warnings.append(
                        f"Suspicious gap of {consecutive_count} consecutive trading sessions "
                        f"through {end_date}"
                    )

        report = ValidationReport(
            is_valid=(len(errors) == 0),
            errors=errors,
            warnings=warnings,
            bars_checked=len(records),
            missing_sessions=missing_sessions_list,
            suspicious_gaps=suspicious_gaps,
        )
        return report

    @classmethod
    def normalize_and_validate(
        cls,
        records: list[OHLCVRecord],
        expected_currency: str | None = None,
        exchange_mic: str | None = None,
    ) -> list[OHLCVRecord]:
        """
        Sort by timestamp, validate all invariants, and return clean records.
        Raises DataValidationError if any hard integrity checks fail.
        """
        if not records:
            return []

        # Sort chronologically by timestamp
        sorted_records = sorted(records, key=lambda r: r.timestamp)
        report = cls.validate_series(
            sorted_records,
            expected_currency=expected_currency,
            exchange_mic=exchange_mic,
        )
        report.raise_for_errors()
        return sorted_records
