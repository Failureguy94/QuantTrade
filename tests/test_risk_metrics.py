"""Unit tests for the VaR / expected-shortfall estimators."""

import numpy as np
import pytest
from scipy import stats

from app.risk.metrics import (
    RiskInputError,
    annualized_volatility,
    drawdown_from_returns,
    historical_var,
    monte_carlo_var,
    parametric_var,
)


@pytest.fixture
def normal_returns() -> np.ndarray:
    return np.random.default_rng(42).normal(0.0005, 0.012, 2000)


def test_historical_var_is_the_empirical_quantile():
    returns = np.linspace(-0.05, 0.05, 101)  # -5% ... +5% in 0.1% steps
    estimate = historical_var(returns, 0.95)
    assert estimate.var == pytest.approx(0.045)
    # Mean of the six worst observations: -5.0% ... -4.5%.
    assert estimate.expected_shortfall == pytest.approx(0.0475)


def test_historical_var_scales_with_the_square_root_of_time():
    returns = np.linspace(-0.05, 0.05, 101)
    one_day = historical_var(returns, 0.95, horizon=1)
    four_day = historical_var(returns, 0.95, horizon=4)
    assert four_day.var == pytest.approx(2 * one_day.var)
    assert four_day.expected_shortfall == pytest.approx(2 * one_day.expected_shortfall)


def test_parametric_var_matches_the_closed_form(normal_returns):
    confidence, horizon = 0.99, 5
    estimate = parametric_var(normal_returns, confidence, horizon)

    mean = normal_returns.mean() * horizon
    sigma = normal_returns.std(ddof=1) * np.sqrt(horizon)
    z = stats.norm.ppf(1 - confidence)
    assert estimate.var == pytest.approx(-(mean + z * sigma))
    assert estimate.expected_shortfall == pytest.approx(
        -mean + sigma * stats.norm.pdf(z) / (1 - confidence)
    )


@pytest.mark.parametrize("method", [historical_var, parametric_var])
def test_expected_shortfall_is_at_least_var(method, normal_returns):
    for confidence in (0.9, 0.95, 0.99):
        estimate = method(normal_returns, confidence)
        assert estimate.expected_shortfall >= estimate.var > 0


def test_higher_confidence_means_higher_var(normal_returns):
    assert historical_var(normal_returns, 0.99).var > historical_var(normal_returns, 0.95).var
    assert parametric_var(normal_returns, 0.99).var > parametric_var(normal_returns, 0.95).var


def test_methods_agree_on_normal_data(normal_returns):
    historical = historical_var(normal_returns, 0.95).var
    parametric = parametric_var(normal_returns, 0.95).var
    simulated = monte_carlo_var(
        normal_returns, np.array([1.0]), 0.95, simulations=100_000, seed=1
    ).var
    assert historical == pytest.approx(parametric, rel=0.1)
    assert simulated == pytest.approx(parametric, rel=0.05)


def test_monte_carlo_is_reproducible_by_seed(normal_returns):
    kwargs = {"simulations": 5000, "confidence": 0.95}
    first = monte_carlo_var(normal_returns, np.array([1.0]), seed=11, **kwargs)
    again = monte_carlo_var(normal_returns, np.array([1.0]), seed=11, **kwargs)
    other = monte_carlo_var(normal_returns, np.array([1.0]), seed=12, **kwargs)
    assert first == again
    assert first != other


def test_monte_carlo_reflects_diversification_and_cash():
    rng = np.random.default_rng(3)
    assets = rng.normal(0.0, 0.02, size=(1500, 2))  # two independent assets

    concentrated = monte_carlo_var(assets, np.array([1.0, 0.0]), 0.95, simulations=50_000, seed=5)
    diversified = monte_carlo_var(assets, np.array([0.5, 0.5]), 0.95, simulations=50_000, seed=5)
    half_cash = monte_carlo_var(assets, np.array([0.5, 0.0]), 0.95, simulations=50_000, seed=5)

    assert diversified.var < concentrated.var
    # Independent, equal-volatility assets: risk falls by about sqrt(2).
    assert diversified.var == pytest.approx(concentrated.var / np.sqrt(2), rel=0.08)
    assert half_cash.var == pytest.approx(concentrated.var / 2, rel=1e-9)


def test_monte_carlo_horizon_increases_risk(normal_returns):
    one = monte_carlo_var(normal_returns, np.array([1.0]), 0.95, 1, simulations=50_000, seed=2)
    ten = monte_carlo_var(normal_returns, np.array([1.0]), 0.95, 10, simulations=50_000, seed=2)
    assert ten.var > 2 * one.var


def test_var_is_never_negative():
    always_up = np.full(60, 0.01) + np.linspace(0, 0.001, 60)
    assert historical_var(always_up, 0.95).var == 0
    assert historical_var(always_up, 0.95).expected_shortfall == 0
    assert parametric_var(always_up, 0.95).var == 0


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda r: historical_var(r[:29], 0.95), "At least 30"),
        (lambda r: parametric_var(r[:10], 0.95), "At least 30"),
        (lambda r: historical_var(r, 1.0), "confidence"),
        (lambda r: historical_var(r, 0.4), "confidence"),
        (lambda r: parametric_var(r, 0.95, horizon=0), "horizon"),
        (lambda r: monte_carlo_var(r, np.array([0.5, 0.5]), 0.95), "one entry per asset"),
        (lambda r: monte_carlo_var(r, np.array([1.0]), 0.95, simulations=0), "simulations"),
        (lambda r: monte_carlo_var(np.full(40, -1.0), np.array([1.0]), 0.95), "log-transformed"),
    ],
)
def test_invalid_inputs(call, message, normal_returns):
    with pytest.raises(RiskInputError, match=message):
        call(normal_returns)


def test_supporting_statistics():
    returns = np.array([0.10, -0.50, 0.20])
    # Wealth path 1 -> 1.1 -> 0.55 -> 0.66: trough is 50% below the 1.1 peak.
    assert drawdown_from_returns(returns) == pytest.approx(-0.5)
    assert drawdown_from_returns(np.array([])) == 0
    assert annualized_volatility(returns, 252) == pytest.approx(
        returns.std(ddof=1) * np.sqrt(252)
    )
    assert annualized_volatility(np.array([0.01]), 252) == 0
