"""Builds persisted risk reports for portfolios and completed backtests."""

import asyncio
import math
import secrets
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableError
from app.core.money import decimal_str, quantize
from app.market_data.service import MarketDataService
from app.market_data.types import PriceAdjustmentMode
from app.models.identity import Instrument
from app.models.portfolio import Position
from app.models.research import Experiment, RiskReport
from app.risk.metrics import (
    TRADING_DAYS_PER_YEAR,
    RiskEstimate,
    RiskInputError,
    RiskMethod,
    annualized_volatility,
    drawdown_from_returns,
    historical_var,
    monte_carlo_var,
    parametric_var,
)
from app.trading.portfolios import get_owned_portfolio


@dataclass(frozen=True)
class RiskParameters:
    method: RiskMethod = RiskMethod.HISTORICAL
    confidence_level: float = 0.95
    horizon_days: int = 1
    lookback_days: int = 252
    simulations: int = 10_000
    random_seed: int | None = None


def _round(value: float, digits: int = 8) -> float | None:
    return round(float(value), digits) if math.isfinite(value) else None


def _estimate(
    params: RiskParameters,
    asset_returns: np.ndarray,
    weights: np.ndarray,
    seed: int | None,
) -> RiskEstimate:
    portfolio_returns = asset_returns @ weights
    if params.method is RiskMethod.HISTORICAL:
        return historical_var(portfolio_returns, params.confidence_level, params.horizon_days)
    if params.method is RiskMethod.PARAMETRIC:
        return parametric_var(portfolio_returns, params.confidence_level, params.horizon_days)
    return monte_carlo_var(
        asset_returns,
        weights,
        params.confidence_level,
        params.horizon_days,
        simulations=params.simulations,
        seed=seed or 0,
    )


def _summarise(
    params: RiskParameters,
    asset_returns: np.ndarray,
    weights: np.ndarray,
    value: Decimal,
    seed: int | None,
    periods_per_year: int,
) -> dict[str, Any]:
    """Run the estimator and describe the sample it was computed from."""
    estimate = _estimate(params, asset_returns, weights, seed)
    portfolio_returns = asset_returns @ weights
    return {
        "method": params.method.value,
        "confidence_level": params.confidence_level,
        "horizon_periods": params.horizon_days,
        "horizon_scaling": (
            "simulated" if params.method is RiskMethod.MONTE_CARLO else "sqrt_time"
        ),
        "observations": int(len(portfolio_returns)),
        "value_at_risk": _round(estimate.var),
        "expected_shortfall": _round(estimate.expected_shortfall),
        "value_at_risk_amount": decimal_str(quantize(value * Decimal(str(estimate.var)))),
        "expected_shortfall_amount": decimal_str(
            quantize(value * Decimal(str(estimate.expected_shortfall)))
        ),
        "portfolio_value": decimal_str(value),
        "annualized_volatility": _round(
            annualized_volatility(portfolio_returns, periods_per_year)
        ),
        "mean_period_return": _round(float(portfolio_returns.mean())),
        "worst_period_return": _round(float(portfolio_returns.min())),
        "best_period_return": _round(float(portfolio_returns.max())),
        "max_drawdown": _round(drawdown_from_returns(portfolio_returns)),
    }


class RiskService:
    def __init__(self, db: AsyncSession, market_data: MarketDataService) -> None:
        self.db = db
        self.market_data = market_data

    @staticmethod
    def _resolve_seed(params: RiskParameters) -> int | None:
        if params.method is not RiskMethod.MONTE_CARLO:
            return None
        return params.random_seed if params.random_seed is not None else secrets.randbits(32)

    @staticmethod
    def _configuration(params: RiskParameters, seed: int | None) -> dict[str, Any]:
        configuration = asdict(params)
        configuration["method"] = params.method.value
        configuration["random_seed"] = seed
        if params.method is not RiskMethod.MONTE_CARLO:
            configuration.pop("simulations")
        return configuration

    async def _save(
        self,
        *,
        user_id: uuid.UUID,
        params: RiskParameters,
        seed: int | None,
        results: dict[str, Any],
        portfolio_id: uuid.UUID | None = None,
        experiment_id: uuid.UUID | None = None,
    ) -> RiskReport:
        report = RiskReport(
            user_id=user_id,
            portfolio_id=portfolio_id,
            experiment_id=experiment_id,
            method=params.method.value,
            configuration=self._configuration(params, seed),
            results=results,
        )
        self.db.add(report)
        await self.db.flush()
        return report

    async def create_portfolio_report(
        self, user_id: uuid.UUID, portfolio_id: uuid.UUID, params: RiskParameters
    ) -> RiskReport:
        """Risk of the portfolio's *current* holdings over the historical window."""
        portfolio = await get_owned_portfolio(self.db, user_id, portfolio_id)
        rows = (
            await self.db.execute(
                select(Position, Instrument.symbol)
                .join(Instrument, Instrument.id == Position.instrument_id)
                .where(Position.portfolio_id == portfolio.id)
                .order_by(Instrument.symbol)
            )
        ).all()
        if not rows:
            raise UnprocessableError("Portfolio has no positions to analyse")

        end = datetime.now(UTC)
        # lookback is in trading days; widen to calendar days with a holiday margin.
        start = end - timedelta(days=math.ceil(params.lookback_days * 365 / 252) + 15)
        closes: dict[str, pd.Series] = {}
        fingerprints: dict[str, str] = {}
        warnings: list[str] = []
        for position, symbol in rows:
            _, bars, fingerprint, notes = await self.market_data.get_covering_market_data(
                position.instrument_id,
                interval="1d",
                adjustment_mode=PriceAdjustmentMode.SPLIT_AND_DIVIDEND,
                start=start,
                end=end,
                limit=params.lookback_days * 2 + 50,
            )
            if not bars:
                raise UnprocessableError(f"No price history is available for {symbol}")
            key = str(position.instrument_id)
            closes[key] = pd.Series(
                [float(bar.close) for bar in bars],
                index=[bar.timestamp.date() for bar in bars],
            )
            fingerprints[key] = fingerprint
            warnings.extend(f"{symbol}: {note}" for note in notes)

        # Exchanges keep different calendars: align on the union of sessions and
        # carry the last close across a market's holidays.
        prices = pd.DataFrame(closes).sort_index().ffill().dropna()
        prices = prices.tail(params.lookback_days + 1)
        returns = prices.pct_change().dropna()

        last_prices = prices.iloc[-1] if len(prices) else None
        if last_prices is None:
            raise UnprocessableError("Positions have no overlapping price history")
        market_values = {
            str(position.instrument_id): position.quantity
            * Decimal(str(last_prices[str(position.instrument_id)]))
            for position, _ in rows
        }
        equity = quantize(portfolio.cash_balance + sum(market_values.values(), Decimal("0")))
        if equity <= 0:
            raise UnprocessableError("Portfolio value is not positive")
        weights = np.array(
            [float(market_values[column] / equity) for column in prices.columns]
        )

        seed = self._resolve_seed(params)
        try:
            results = await asyncio.to_thread(
                _summarise,
                params,
                returns.to_numpy(),
                weights,
                equity,
                seed,
                TRADING_DAYS_PER_YEAR,
            )
        except RiskInputError as exc:
            raise UnprocessableError(str(exc)) from exc

        symbols = {str(position.instrument_id): symbol for position, symbol in rows}
        results.update(
            {
                "subject": "PORTFOLIO",
                "currency": portfolio.base_currency,
                "as_of": prices.index[-1].isoformat(),
                "cash_weight": _round(float(portfolio.cash_balance / equity)),
                "positions": [
                    {
                        "instrument_id": column,
                        "symbol": symbols[column],
                        "weight": _round(float(weight)),
                        "market_value": decimal_str(quantize(market_values[column])),
                    }
                    for column, weight in zip(prices.columns, weights, strict=True)
                ],
                "data_fingerprints": fingerprints,
                "warnings": warnings,
            }
        )
        return await self._save(
            user_id=user_id,
            params=params,
            seed=seed,
            results=results,
            portfolio_id=portfolio.id,
        )

    async def create_experiment_report(
        self, user_id: uuid.UUID, experiment_id: uuid.UUID, params: RiskParameters
    ) -> RiskReport:
        """Risk of a completed backtest, from the returns of its equity curve."""
        experiment = (
            await self.db.execute(
                select(Experiment).where(
                    Experiment.id == experiment_id, Experiment.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if experiment is None:
            raise NotFoundError("Experiment not found")
        curve = (experiment.results or {}).get("equity_curve")
        if experiment.status != "COMPLETED" or not curve:
            raise UnprocessableError("Experiment has no completed results to analyse")

        equity = np.array([float(point["equity"]) for point in curve], dtype=np.float64)
        if np.any(equity <= 0):
            raise UnprocessableError("Equity curve reaches zero; returns are undefined")
        returns = (equity[1:] / equity[:-1] - 1.0)[-params.lookback_days :]
        periods_per_year = int(
            (experiment.results or {}).get("metrics", {}).get("periods_per_year")
            or TRADING_DAYS_PER_YEAR
        )

        seed = self._resolve_seed(params)
        try:
            results = await asyncio.to_thread(
                _summarise,
                params,
                returns.reshape(-1, 1),
                np.array([1.0]),
                Decimal(curve[-1]["equity"]),
                seed,
                periods_per_year,
            )
        except RiskInputError as exc:
            raise UnprocessableError(str(exc)) from exc

        results.update(
            {
                "subject": "EXPERIMENT",
                "as_of": curve[-1]["timestamp"],
                "periods_per_year": periods_per_year,
                "data_fingerprint": experiment.data_fingerprint,
            }
        )
        return await self._save(
            user_id=user_id,
            params=params,
            seed=seed,
            results=results,
            experiment_id=experiment.id,
        )
