"""Decimal helpers for ledger-boundary arithmetic (ADR-004)."""

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal

# Matches the NUMERIC(24, 8) columns used for cash, quantities and prices.
LEDGER_QUANTUM = Decimal("0.00000001")
BPS = Decimal("10000")
ZERO = Decimal("0")
# Largest magnitude a NUMERIC(24, 8) column can hold is just under 10^16.
MAX_LEDGER_VALUE = Decimal("9999999999999999.99999999")


def quantize(value: Decimal) -> Decimal:
    """Round to ledger precision using banker's rounding."""
    return value.quantize(LEDGER_QUANTUM, rounding=ROUND_HALF_EVEN)


def quantize_down(value: Decimal) -> Decimal:
    """Truncate to ledger precision (never rounds a quantity up)."""
    return value.quantize(LEDGER_QUANTUM, rounding=ROUND_DOWN)


def decimal_str(value: Decimal) -> str:
    """Plain decimal notation. ``str(Decimal("0E-8"))`` would give ``"0E-8"``."""
    return format(value, "f")


def bps_to_rate(bps: Decimal) -> Decimal:
    return bps / BPS


def fits_ledger_precision(value: Decimal) -> bool:
    """True when ``value`` has no more than eight decimal places."""
    return value == value.quantize(LEDGER_QUANTUM, rounding=ROUND_DOWN)
