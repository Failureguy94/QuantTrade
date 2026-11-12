"""Backtesting: strategy catalogue, simulation engine and performance metrics."""

from app.backtesting.engine import (
    ENGINE_VERSION,
    BacktestConfig,
    BacktestError,
    BacktestResult,
    run_backtest,
)
from app.backtesting.strategies import (
    STRATEGY_REGISTRY,
    InvalidStrategyParametersError,
    UnknownStrategyTypeError,
    generate_signals,
    get_strategy_definition,
    validate_parameters,
)

__all__ = [
    "ENGINE_VERSION",
    "BacktestConfig",
    "BacktestError",
    "BacktestResult",
    "run_backtest",
    "STRATEGY_REGISTRY",
    "InvalidStrategyParametersError",
    "UnknownStrategyTypeError",
    "generate_signals",
    "get_strategy_definition",
    "validate_parameters",
]
