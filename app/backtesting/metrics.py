"""Performance statistics computed from an equity curve.

Statistical work uses NumPy floating point (ADR-004); monetary inputs are
converted explicitly by the caller.
"""

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import numpy as np

PERIODS_PER_YEAR: dict[str, int] = {"1d": 252, "1wk": 52, "1mo": 12}


def _finite(value: float | None, digits: int = 8) -> float | None:
    """JSON has no NaN/Infinity: map non-finite statistics to None."""
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def period_returns(equity: np.ndarray) -> np.ndarray:
    """Simple per-bar returns of an equity curve."""
    equity = np.asarray(equity, dtype=np.float64)
    if len(equity) < 2:
        return np.array([], dtype=np.float64)
    previous = equity[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = equity[1:] / previous - 1.0
    return np.where(np.isfinite(returns), returns, 0.0)


def max_drawdown(equity: np.ndarray) -> tuple[float, int]:
    """Return (worst peak-to-trough decline as a negative fraction, longest bars under water)."""
    equity = np.asarray(equity, dtype=np.float64)
    if len(equity) == 0:
        return 0.0, 0
    running_peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdown = np.where(running_peak > 0, equity / running_peak - 1.0, 0.0)

    longest = current = 0
    for underwater in drawdown < 0:
        current = current + 1 if underwater else 0
        longest = max(longest, current)
    return float(drawdown.min()), longest


def compute_performance_metrics(
    timestamps: Sequence[datetime],
    equity: Sequence[float],
    *,
    periods_per_year: int,
    risk_free_rate: float = 0.0,
    trade_pnls: Sequence[float] = (),
    trade_returns: Sequence[float] = (),
    bars_in_market: int = 0,
    benchmark_closes: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Summarise an equity curve.

    ``risk_free_rate`` is an annual rate. Ratios that are undefined for the sample
    (for example Sharpe with zero volatility) are reported as ``None``.
    """
    curve = np.asarray(equity, dtype=np.float64)
    returns = period_returns(curve)
    n_bars = len(curve)

    initial = float(curve[0]) if n_bars else 0.0
    final = float(curve[-1]) if n_bars else 0.0
    total_return = final / initial - 1.0 if initial > 0 else None

    cagr: float | None = None
    if n_bars >= 2 and initial > 0 and final > 0:
        elapsed_years = (timestamps[-1] - timestamps[0]).total_seconds() / (365.25 * 86400)
        if elapsed_years > 0:
            cagr = (final / initial) ** (1.0 / elapsed_years) - 1.0

    volatility: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    if len(returns) >= 2:
        rf_per_period = risk_free_rate / periods_per_year
        excess = returns - rf_per_period
        std = float(np.std(returns, ddof=1))
        volatility = std * math.sqrt(periods_per_year)
        if std > 0:
            sharpe = float(np.mean(excess)) / std * math.sqrt(periods_per_year)
        downside = np.minimum(excess, 0.0)
        downside_deviation = math.sqrt(float(np.mean(downside**2)))
        if downside_deviation > 0:
            sortino = float(np.mean(excess)) / downside_deviation * math.sqrt(periods_per_year)

    worst_drawdown, drawdown_bars = max_drawdown(curve)
    calmar = cagr / abs(worst_drawdown) if cagr is not None and worst_drawdown < 0 else None

    pnls = np.asarray(trade_pnls, dtype=np.float64)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    win_rate = len(wins) / len(pnls) if len(pnls) else None
    profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) else None
    avg_trade_return = float(np.mean(trade_returns)) if len(trade_returns) else None

    benchmark_return: float | None = None
    if benchmark_closes is not None and len(benchmark_closes) >= 2 and benchmark_closes[0] > 0:
        benchmark_return = benchmark_closes[-1] / benchmark_closes[0] - 1.0

    return {
        "bars": n_bars,
        "total_return": _finite(total_return),
        "cagr": _finite(cagr),
        "annualized_volatility": _finite(volatility),
        "sharpe_ratio": _finite(sharpe),
        "sortino_ratio": _finite(sortino),
        "max_drawdown": _finite(worst_drawdown),
        "max_drawdown_duration_bars": drawdown_bars,
        "calmar_ratio": _finite(calmar),
        "closed_trades": int(len(pnls)),
        "win_rate": _finite(win_rate),
        "profit_factor": _finite(profit_factor),
        "average_trade_return": _finite(avg_trade_return),
        "exposure": _finite(bars_in_market / n_bars if n_bars else None),
        "benchmark_buy_and_hold_return": _finite(benchmark_return),
        "periods_per_year": periods_per_year,
        "risk_free_rate": risk_free_rate,
    }
