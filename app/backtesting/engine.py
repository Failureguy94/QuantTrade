"""Single-instrument, bar-driven backtest engine.

Timing model (no look-ahead):

* the strategy's signal for bar ``i`` is computed from closes up to and including
  bar ``i``;
* the resulting order is filled at the **open of bar ``i + 1``**, moved against
  the trader by ``slippage_bps`` and charged ``commission_bps`` of notional;
* the account is marked to market at each bar's close.

Cash, quantities and fills are tracked in ``Decimal``; statistics are derived
from the resulting equity curve in floating point (ADR-004).

Simplifications, stated so results are not over-read: fills are assumed for the
full size at the adjusted open with no volume limit, short positions carry no
borrow cost or margin requirement, and idle cash earns nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

import numpy as np

from app.backtesting.metrics import compute_performance_metrics
from app.core.money import ZERO, bps_to_rate, decimal_str, quantize, quantize_down
from app.market_data.types import OHLCVRecord

ENGINE_VERSION = "1.0.0"


class BacktestError(ValueError):
    """The backtest inputs cannot produce a meaningful simulation."""


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: Decimal
    commission_bps: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("2")
    position_size: Decimal = Decimal("1")
    allow_short: bool = False
    fractional_shares: bool = False
    periods_per_year: int = 252
    risk_free_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise BacktestError("initial_capital must be positive")
        if self.commission_bps < 0 or self.slippage_bps < 0:
            raise BacktestError("commission_bps and slippage_bps must not be negative")
        if not (ZERO < self.position_size <= 1):
            raise BacktestError("position_size must be in (0, 1]")


@dataclass
class RoundTrip:
    """An entry and (once closed) its matching exit."""

    side: str
    quantity: Decimal
    entry_time: datetime
    entry_price: Decimal
    entry_bar: int
    entry_cash_flow: Decimal  # signed cash impact of the entry, commission included
    commission: Decimal
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_bar: int | None = None
    pnl: Decimal = ZERO
    return_pct: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_time is None

    @property
    def entry_notional(self) -> Decimal:
        return self.quantity * self.entry_price


@dataclass
class BacktestResult:
    timestamps: list[datetime]
    equity: list[Decimal]
    positions: list[Decimal]
    trades: list[RoundTrip]
    total_commission: Decimal
    total_slippage: Decimal
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """JSON-serialisable view. Monetary values are exact decimal strings."""
        return {
            "metrics": self.metrics,
            "final_equity": decimal_str(self.equity[-1]),
            "total_commission": decimal_str(self.total_commission),
            "total_slippage": decimal_str(self.total_slippage),
            "equity_curve": [
                {
                    "timestamp": ts.isoformat(),
                    "equity": decimal_str(eq),
                    "position": decimal_str(pos),
                }
                for ts, eq, pos in zip(self.timestamps, self.equity, self.positions, strict=True)
            ],
            "trades": [
                {
                    "side": trade.side,
                    "quantity": decimal_str(trade.quantity),
                    "entry_time": trade.entry_time.isoformat(),
                    "entry_price": decimal_str(trade.entry_price),
                    "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
                    "exit_price": decimal_str(trade.exit_price) if trade.exit_price else None,
                    "bars_held": (
                        (trade.exit_bar if trade.exit_bar is not None else len(self.timestamps) - 1)
                        - trade.entry_bar
                    ),
                    "commission": decimal_str(trade.commission),
                    "pnl": decimal_str(trade.pnl),
                    "return_pct": round(trade.return_pct, 8),
                    "is_open": trade.is_open,
                }
                for trade in self.trades
            ],
            "warnings": self.warnings,
        }


class _Account:
    """Mutable simulation state with the fill arithmetic in one place."""

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.cash = config.initial_capital
        self.quantity = ZERO  # signed: negative while short
        self.commission_rate = bps_to_rate(config.commission_bps)
        self.slippage_rate = bps_to_rate(config.slippage_bps)
        self.total_commission = ZERO
        self.total_slippage = ZERO
        self.trades: list[RoundTrip] = []
        self.open_trade: RoundTrip | None = None
        self.warnings: list[str] = []
        self._undersized_warned = False

    @property
    def direction(self) -> int:
        return 1 if self.quantity > 0 else -1 if self.quantity < 0 else 0

    def _fill_price(self, reference: Decimal, buying: bool) -> Decimal:
        adjustment = 1 + self.slippage_rate if buying else 1 - self.slippage_rate
        return quantize(reference * adjustment)

    def _size(self, budget: Decimal, unit_cost: Decimal) -> Decimal:
        raw = budget / unit_cost
        if self.config.fractional_shares:
            return quantize_down(raw)
        return raw.quantize(Decimal("1"), rounding=ROUND_DOWN)

    def open_position(self, direction: int, bar: OHLCVRecord, index: int) -> None:
        buying = direction > 0
        price = self._fill_price(bar.open, buying)
        budget = self.cash * self.config.position_size
        # A buy must also fund its commission out of the same budget.
        unit_cost = price * (1 + self.commission_rate) if buying else price
        quantity = self._size(budget, unit_cost) if budget > 0 else ZERO
        if quantity <= 0:
            if not self._undersized_warned:
                self._undersized_warned = True
                self.warnings.append(
                    f"{bar.timestamp.date()}: entry skipped, {quantize(budget)} of capital "
                    f"cannot buy one unit at {price} (later occurrences are not repeated)"
                )
            return

        notional = quantize(quantity * price)
        commission = quantize(notional * self.commission_rate)
        cash_flow = -(notional + commission) if buying else notional - commission
        self.cash += cash_flow
        self.quantity = quantity if buying else -quantity
        self.total_commission += commission
        self.total_slippage += quantize(quantity * abs(price - bar.open))
        self.open_trade = RoundTrip(
            side="LONG" if buying else "SHORT",
            quantity=quantity,
            entry_time=bar.timestamp,
            entry_price=price,
            entry_bar=index,
            entry_cash_flow=cash_flow,
            commission=commission,
        )
        self.trades.append(self.open_trade)

    def close_position(self, reference: Decimal, timestamp: datetime, index: int) -> None:
        trade = self.open_trade
        if trade is None or self.quantity == 0:
            return
        buying = self.quantity < 0  # covering a short
        price = self._fill_price(reference, buying)
        quantity = abs(self.quantity)
        notional = quantize(quantity * price)
        commission = quantize(notional * self.commission_rate)
        cash_flow = -(notional + commission) if buying else notional - commission
        self.cash += cash_flow
        self.quantity = ZERO
        self.total_commission += commission
        self.total_slippage += quantize(quantity * abs(price - reference))

        trade.exit_time = timestamp
        trade.exit_price = price
        trade.exit_bar = index
        trade.commission += commission
        trade.pnl = trade.entry_cash_flow + cash_flow
        if trade.entry_notional > 0:
            trade.return_pct = float(trade.pnl / trade.entry_notional)
        self.open_trade = None

    def mark_open_trade(self, close: Decimal) -> None:
        """Value a still-open trade at ``close`` without booking an exit."""
        trade = self.open_trade
        if trade is None:
            return
        trade.pnl = trade.entry_cash_flow + self.quantity * close
        if trade.entry_notional > 0:
            trade.return_pct = float(trade.pnl / trade.entry_notional)


def run_backtest(
    bars: Sequence[OHLCVRecord],
    signals: Sequence[int] | np.ndarray,
    config: BacktestConfig,
) -> BacktestResult:
    """Simulate trading ``signals`` over ``bars`` (chronological, one instrument)."""
    if len(bars) < 2:
        raise BacktestError("At least two bars are required to run a backtest")
    if len(signals) != len(bars):
        raise BacktestError("signals and bars must have the same length")
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.timestamp <= previous.timestamp:
            raise BacktestError("Bars must be strictly increasing in time")

    account = _Account(config)
    timestamps: list[datetime] = []
    equity: list[Decimal] = []
    positions: list[Decimal] = []
    bars_in_market = 0
    liquidated = False

    for index, bar in enumerate(bars):
        if index > 0 and not liquidated:
            target = int(np.sign(signals[index - 1]))
            if target < 0 and not config.allow_short:
                target = 0
            if target != account.direction:
                account.close_position(bar.open, bar.timestamp, index)
                if target != 0:
                    account.open_position(target, bar, index)

        value = account.cash + account.quantity * bar.close
        if value <= 0 and account.quantity != 0:
            # Losses on a short exceeded the account. Stop trading rather than
            # report a curve that keeps compounding from negative equity.
            account.close_position(bar.close, bar.timestamp, index)
            value = account.cash
            liquidated = True
            account.warnings.append(
                f"{bar.timestamp.date()}: equity was exhausted; position liquidated "
                "and trading halted"
            )

        if account.quantity != 0:
            bars_in_market += 1
        timestamps.append(bar.timestamp)
        equity.append(quantize(value))
        positions.append(account.quantity)

    account.mark_open_trade(bars[-1].close)
    closed = [trade for trade in account.trades if not trade.is_open]
    metrics = compute_performance_metrics(
        timestamps,
        [float(value) for value in equity],
        periods_per_year=config.periods_per_year,
        risk_free_rate=config.risk_free_rate,
        trade_pnls=[float(trade.pnl) for trade in closed],
        trade_returns=[trade.return_pct for trade in closed],
        bars_in_market=bars_in_market,
        benchmark_closes=[float(bar.close) for bar in bars],
    )
    metrics["open_trades"] = len(account.trades) - len(closed)

    return BacktestResult(
        timestamps=timestamps,
        equity=equity,
        positions=positions,
        trades=account.trades,
        total_commission=account.total_commission,
        total_slippage=account.total_slippage,
        metrics=metrics,
        warnings=account.warnings,
    )
