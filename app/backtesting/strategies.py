"""Built-in strategy catalogue: parameter validation and signal generation.

A strategy maps a series of closing prices to a *target exposure* per bar:
``1`` (long), ``0`` (flat) or ``-1`` (short). The value at index ``i`` may only
depend on prices at indices ``<= i``. The engine acts on it at the open of bar
``i + 1``, so no signal can ever trade on information from its own future
(architecture rule 6).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class UnknownStrategyTypeError(ValueError):
    """Requested strategy type is not in the catalogue."""


class InvalidStrategyParametersError(ValueError):
    """Parameters do not satisfy the strategy type's schema."""

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class StrategyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuyAndHoldParams(StrategyParams):
    """No parameters: enter on the first tradable bar and hold."""


class SmaCrossoverParams(StrategyParams):
    fast_window: int = Field(default=20, ge=1, le=1000, description="Fast SMA length in bars")
    slow_window: int = Field(default=50, ge=2, le=2000, description="Slow SMA length in bars")

    @model_validator(mode="after")
    def fast_must_be_shorter(self) -> "SmaCrossoverParams":
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        return self


class MomentumParams(StrategyParams):
    lookback: int = Field(default=60, ge=1, le=2000, description="Return lookback in bars")
    threshold: float = Field(
        default=0.0, ge=0.0, le=10.0, description="Minimum absolute lookback return to act on"
    )


class MeanReversionParams(StrategyParams):
    lookback: int = Field(default=20, ge=2, le=2000, description="Rolling window in bars")
    entry_z: float = Field(default=2.0, gt=0.0, le=10.0, description="Z-score to enter at")
    exit_z: float = Field(default=0.5, ge=0.0, le=10.0, description="Z-score to exit at")

    @model_validator(mode="after")
    def exit_inside_entry(self) -> "MeanReversionParams":
        if self.exit_z >= self.entry_z:
            raise ValueError("exit_z must be smaller than entry_z")
        return self


def _buy_and_hold(closes: np.ndarray, params: BuyAndHoldParams) -> np.ndarray:
    return np.ones(len(closes), dtype=np.int8)


def _sma_crossover(closes: np.ndarray, params: SmaCrossoverParams) -> np.ndarray:
    series = pd.Series(closes)
    fast = series.rolling(params.fast_window).mean().to_numpy()
    slow = series.rolling(params.slow_window).mean().to_numpy()
    signal = np.zeros(len(closes), dtype=np.int8)
    ready = ~np.isnan(slow)
    signal[ready & (fast > slow)] = 1
    signal[ready & (fast < slow)] = -1
    return signal


def _momentum(closes: np.ndarray, params: MomentumParams) -> np.ndarray:
    signal = np.zeros(len(closes), dtype=np.int8)
    if len(closes) <= params.lookback:
        return signal
    past = closes[: -params.lookback]
    trailing_return = closes[params.lookback :] / past - 1.0
    tail = signal[params.lookback :]
    tail[trailing_return > params.threshold] = 1
    tail[trailing_return < -params.threshold] = -1
    return signal


def _mean_reversion(closes: np.ndarray, params: MeanReversionParams) -> np.ndarray:
    series = pd.Series(closes)
    mean = series.rolling(params.lookback).mean().to_numpy()
    std = series.rolling(params.lookback).std(ddof=1).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        zscore = (closes - mean) / std

    signal = np.zeros(len(closes), dtype=np.int8)
    state = 0
    for i, z in enumerate(zscore):
        if np.isfinite(z):
            if state == 0:
                if z <= -params.entry_z:
                    state = 1
                elif z >= params.entry_z:
                    state = -1
            elif state == 1 and z >= -params.exit_z:
                state = 0
            elif state == -1 and z <= params.exit_z:
                state = 0
        # A non-finite z-score (warm-up or a flat window) carries no information,
        # so the previous state is held.
        signal[i] = state
    return signal


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_type: str
    name: str
    description: str
    params_model: type[StrategyParams]
    generate: Callable[[np.ndarray, Any], np.ndarray]

    def parameter_schema(self) -> dict[str, Any]:
        return self.params_model.model_json_schema()

    def default_parameters(self) -> dict[str, Any]:
        return self.params_model().model_dump()


STRATEGY_REGISTRY: dict[str, StrategyDefinition] = {
    definition.strategy_type: definition
    for definition in (
        StrategyDefinition(
            strategy_type="BUY_AND_HOLD",
            name="Buy and hold",
            description="Always long. Useful as a benchmark for the other strategies.",
            params_model=BuyAndHoldParams,
            generate=_buy_and_hold,
        ),
        StrategyDefinition(
            strategy_type="SMA_CROSSOVER",
            name="Moving-average crossover",
            description=(
                "Long while the fast simple moving average is above the slow one; "
                "short (if enabled) while it is below."
            ),
            params_model=SmaCrossoverParams,
            generate=_sma_crossover,
        ),
        StrategyDefinition(
            strategy_type="MOMENTUM",
            name="Time-series momentum",
            description=(
                "Long when the trailing lookback return exceeds the threshold; "
                "short (if enabled) when it is below the negative threshold."
            ),
            params_model=MomentumParams,
            generate=_momentum,
        ),
        StrategyDefinition(
            strategy_type="MEAN_REVERSION",
            name="Z-score mean reversion",
            description=(
                "Buys when price falls entry_z rolling standard deviations below its "
                "rolling mean and exits when it reverts to within exit_z; mirrored for "
                "shorts if enabled."
            ),
            params_model=MeanReversionParams,
            generate=_mean_reversion,
        ),
    )
}


def get_strategy_definition(strategy_type: str) -> StrategyDefinition:
    definition = STRATEGY_REGISTRY.get(strategy_type.strip().upper())
    if definition is None:
        raise UnknownStrategyTypeError(
            f"Unknown strategy type '{strategy_type}'. "
            f"Supported: {', '.join(sorted(STRATEGY_REGISTRY))}"
        )
    return definition


def validate_parameters(strategy_type: str, parameters: dict[str, Any] | None) -> dict[str, Any]:
    """Validate ``parameters`` for ``strategy_type`` and return them with defaults applied."""
    definition = get_strategy_definition(strategy_type)
    try:
        model = definition.params_model.model_validate(parameters or {})
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
            for err in exc.errors()
        ]
        summary = "; ".join(
            f"{err['field']}: {err['message']}" if err["field"] else err["message"]
            for err in errors
        )
        raise InvalidStrategyParametersError(
            f"Invalid parameters for {definition.strategy_type}: {summary}", errors=errors
        ) from exc
    return model.model_dump()


def generate_signals(
    strategy_type: str, parameters: dict[str, Any] | None, closes: np.ndarray
) -> np.ndarray:
    """Return the target exposure (-1, 0, 1) for every bar of ``closes``."""
    definition = get_strategy_definition(strategy_type)
    params = definition.params_model.model_validate(parameters or {})
    prices = np.asarray(closes, dtype=np.float64)
    if prices.ndim != 1:
        raise ValueError("closes must be a one-dimensional price series")
    return definition.generate(prices, params)
