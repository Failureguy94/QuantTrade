"""Value-at-Risk and expected-shortfall estimators.

All functions take *simple* per-period returns and report losses as positive
fractions of portfolio value: a VaR of 0.02 means "a loss of 2% or worse is
expected with probability ``1 - confidence``".

Multi-period horizons use square-root-of-time scaling for the historical and
parametric methods (i.i.d. returns assumed). The Monte Carlo method simulates
the horizon directly from a multivariate normal fit of log returns.

Computation is floating point (ADR-004); callers convert at the ledger boundary.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy import stats

MIN_OBSERVATIONS = 30
TRADING_DAYS_PER_YEAR = 252


class RiskMethod(StrEnum):
    HISTORICAL = "HISTORICAL"
    PARAMETRIC = "PARAMETRIC"
    MONTE_CARLO = "MONTE_CARLO"


class RiskInputError(ValueError):
    """The return sample cannot support the requested estimate."""


@dataclass(frozen=True)
class RiskEstimate:
    var: float
    expected_shortfall: float


def _check_inputs(sample_size: int, confidence: float, horizon: int) -> None:
    if not 0.5 < confidence < 1.0:
        raise RiskInputError("confidence must be between 0.5 and 1 (exclusive)")
    if horizon < 1:
        raise RiskInputError("horizon must be at least one period")
    if sample_size < MIN_OBSERVATIONS:
        raise RiskInputError(
            f"At least {MIN_OBSERVATIONS} return observations are required, got {sample_size}"
        )


def _tail_estimate(pnl: np.ndarray, confidence: float) -> RiskEstimate:
    """Empirical VaR / ES of a profit-and-loss sample (losses are negative values)."""
    cutoff = float(np.quantile(pnl, 1.0 - confidence))
    tail = pnl[pnl <= cutoff]
    shortfall = float(tail.mean()) if len(tail) else cutoff
    return RiskEstimate(var=max(-cutoff, 0.0), expected_shortfall=max(-shortfall, 0.0))


def historical_var(returns: np.ndarray, confidence: float, horizon: int = 1) -> RiskEstimate:
    """Historical simulation: the empirical loss quantile of observed returns."""
    returns = np.asarray(returns, dtype=np.float64)
    _check_inputs(len(returns), confidence, horizon)
    one_period = _tail_estimate(returns, confidence)
    scale = math.sqrt(horizon)
    return RiskEstimate(one_period.var * scale, one_period.expected_shortfall * scale)


def parametric_var(returns: np.ndarray, confidence: float, horizon: int = 1) -> RiskEstimate:
    """Variance-covariance (Gaussian) VaR from the sample mean and volatility."""
    returns = np.asarray(returns, dtype=np.float64)
    _check_inputs(len(returns), confidence, horizon)
    mean = float(returns.mean()) * horizon
    sigma = float(returns.std(ddof=1)) * math.sqrt(horizon)
    z = float(stats.norm.ppf(1.0 - confidence))  # negative
    var = -(mean + z * sigma)
    shortfall = -mean + sigma * float(stats.norm.pdf(z)) / (1.0 - confidence)
    return RiskEstimate(var=max(var, 0.0), expected_shortfall=max(shortfall, 0.0))


def monte_carlo_var(
    asset_returns: np.ndarray,
    weights: np.ndarray,
    confidence: float,
    horizon: int = 1,
    *,
    simulations: int = 10_000,
    seed: int = 0,
) -> RiskEstimate:
    """Monte Carlo VaR.

    ``asset_returns`` is (observations x assets) of simple returns and ``weights``
    the fraction of portfolio value in each asset (cash is the remainder and does
    not move). Horizon log returns are drawn from N(h * mu, h * Sigma).
    """
    asset_returns = np.asarray(asset_returns, dtype=np.float64)
    if asset_returns.ndim == 1:
        asset_returns = asset_returns.reshape(-1, 1)
    weights = np.asarray(weights, dtype=np.float64).reshape(-1)
    if asset_returns.shape[1] != len(weights):
        raise RiskInputError("weights must have one entry per asset")
    _check_inputs(asset_returns.shape[0], confidence, horizon)
    if simulations < 1:
        raise RiskInputError("simulations must be positive")
    if np.any(asset_returns <= -1.0):
        raise RiskInputError("returns of -100% or worse cannot be log-transformed")

    log_returns = np.log1p(asset_returns)
    mean = log_returns.mean(axis=0) * horizon
    covariance = np.atleast_2d(np.cov(log_returns, rowvar=False, ddof=1)) * horizon

    rng = np.random.default_rng(seed)
    draws = rng.multivariate_normal(mean, covariance, size=simulations, method="svd")
    portfolio_pnl = np.expm1(draws) @ weights
    return _tail_estimate(portfolio_pnl, confidence)


def annualized_volatility(returns: np.ndarray, periods_per_year: int) -> float:
    returns = np.asarray(returns, dtype=np.float64)
    if len(returns) < 2:
        return 0.0
    return float(returns.std(ddof=1)) * math.sqrt(periods_per_year)


def drawdown_from_returns(returns: np.ndarray) -> float:
    """Worst peak-to-trough decline of the compounded return path (negative fraction)."""
    returns = np.asarray(returns, dtype=np.float64)
    if len(returns) == 0:
        return 0.0
    wealth = np.concatenate(([1.0], np.cumprod(1.0 + returns)))
    peaks = np.maximum.accumulate(wealth)
    return float((wealth / peaks - 1.0).min())
