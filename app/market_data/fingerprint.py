"""Cryptographic data fingerprinting for market data snapshot reproducibility."""

import hashlib
from collections.abc import Sequence

from app.market_data.types import OHLCVRecord


def compute_dataset_fingerprint(records: Sequence[OHLCVRecord]) -> str:
    """
    Generate a deterministic SHA-256 digest over a sequence of OHLCV records.
    
    Guarantees that any research experiment, backtest, or risk calculation
    can record an immutable, auditable fingerprint of the exact market data
    snapshot used.
    
    Invariants:
    - Input records are sorted chronologically.
    - Prices formatted as normalized exact string decimals.
    - Volume formatted as integer string.
    - Timestamps formatted as ISO UTC strings.
    """
    if not records:
        return "empty_snapshot"

    sorted_records = sorted(records, key=lambda r: r.timestamp)
    hasher = hashlib.sha256()

    # Include header with provenance metadata.
    # Fields: instrument_id, interval, adjustment_mode, currency, provider.
    # All five dimensions must be in the header so that snapshots that differ
    # only in provider or adjustment mode produce different fingerprints.
    first = sorted_records[0]
    header = (
        f"{first.instrument_id}|{first.interval}|{first.adjustment_mode}"
        f"|{first.currency}|{first.provider}\n"
    )
    hasher.update(header.encode("utf-8"))

    for bar in sorted_records:
        adj_str = str(bar.adjusted_close) if bar.adjusted_close is not None else "NONE"
        row = (
            f"{bar.timestamp.isoformat()},"
            f"{bar.open},"
            f"{bar.high},"
            f"{bar.low},"
            f"{bar.close},"
            f"{adj_str},"
            f"{bar.volume}\n"
        )
        hasher.update(row.encode("utf-8"))

    return hasher.hexdigest()
