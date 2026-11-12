"""Unit tests for the strategy catalogue, backtest engine and performance metrics."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from app.backtesting.engine import BacktestConfig, BacktestError, run_backtest
from app.backtesting.metrics import compute_performance_metrics, max_drawdown
from app.backtesting.strategies import (
    STRATEGY_REGISTRY,
    InvalidStrategyParametersError,
    UnknownStrategyTypeError,
    generate_signals,
    validate_parameters,
)
from helpers import make_bars, wave_closes

NO_COSTS = {"commission_bps": Decimal("0"), "slippage_bps": Decimal("0")}


def config(capital: str = "10000", **overrides) -> BacktestConfig:
    return BacktestConfig(initial_capital=Decimal(capital), **{**NO_COSTS, **overrides})


# ---------------------------------------------------------------------------
# Strategy parameters
# ---------------------------------------------------------------------------
class TestStrategyParameters:
    def test_defaults_are_applied(self):
        assert validate_parameters("SMA_CROSSOVER", {}) == {"fast_window": 20, "slow_window": 50}
        assert validate_parameters("sma_crossover", {"fast_window": 5}) == {
            "fast_window": 5,
            "slow_window": 50,
        }
        assert validate_parameters("BUY_AND_HOLD", None) == {}

    def test_unknown_type_is_rejected(self):
        with pytest.raises(UnknownStrategyTypeError, match="Supported: BUY_AND_HOLD"):
            validate_parameters("STAT_ARB", {})

    @pytest.mark.parametrize(
        ("strategy_type", "parameters", "fragment"),
        [
            ("SMA_CROSSOVER", {"fast_window": 50, "slow_window": 20}, "fast_window must be"),
            ("SMA_CROSSOVER", {"fast_window": 0}, "fast_window"),
            ("SMA_CROSSOVER", {"window": 10}, "window"),
            ("MOMENTUM", {"lookback": -1}, "lookback"),
            ("MEAN_REVERSION", {"entry_z": 1.0, "exit_z": 1.5}, "exit_z must be"),
            ("BUY_AND_HOLD", {"anything": 1}, "anything"),
        ],
    )
    def test_invalid_parameters_are_rejected(self, strategy_type, parameters, fragment):
        with pytest.raises(InvalidStrategyParametersError, match=fragment):
            validate_parameters(strategy_type, parameters)

    def test_every_type_publishes_a_schema_and_valid_defaults(self):
        for definition in STRATEGY_REGISTRY.values():
            assert definition.parameter_schema()["type"] == "object"
            defaults = definition.default_parameters()
            assert validate_parameters(definition.strategy_type, defaults) == defaults


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
class TestSignals:
    def test_buy_and_hold_is_always_long(self):
        signals = generate_signals("BUY_AND_HOLD", {}, np.array([1.0, 2.0, 3.0]))
        assert signals.tolist() == [1, 1, 1]

    def test_sma_crossover_follows_the_averages(self):
        closes = np.array([1, 2, 3, 4, 5, 4, 3, 2, 1], dtype=float)
        signals = generate_signals(
            "SMA_CROSSOVER", {"fast_window": 2, "slow_window": 3}, closes
        )
        # Flat until the slow average exists, long while fast > slow, then short.
        assert signals.tolist() == [0, 0, 1, 1, 1, 1, -1, -1, -1]

    def test_momentum_uses_the_trailing_return_and_threshold(self):
        closes = np.array([100, 100, 110, 100, 90], dtype=float)
        signals = generate_signals("MOMENTUM", {"lookback": 2, "threshold": 0.05}, closes)
        assert signals.tolist() == [0, 0, 1, 0, -1]

    def test_momentum_with_too_little_history_stays_flat(self):
        signals = generate_signals("MOMENTUM", {"lookback": 10}, np.array([1.0, 2.0, 3.0]))
        assert signals.tolist() == [0, 0, 0]

    def test_mean_reversion_enters_on_a_dip_and_exits_on_reversion(self):
        closes = np.array([100.0] * 5 + [90.0] + [100.0] * 5)
        signals = generate_signals(
            "MEAN_REVERSION", {"lookback": 5, "entry_z": 1.5, "exit_z": 0.5}, closes
        )
        assert signals.tolist() == [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0]

    @pytest.mark.parametrize(
        ("strategy_type", "parameters"),
        [
            ("BUY_AND_HOLD", {}),
            ("SMA_CROSSOVER", {"fast_window": 5, "slow_window": 20}),
            ("MOMENTUM", {"lookback": 15, "threshold": 0.01}),
            ("MEAN_REVERSION", {"lookback": 10, "entry_z": 1.0, "exit_z": 0.2}),
        ],
    )
    def test_signals_never_look_ahead(self, strategy_type, parameters):
        """A signal must not change when later prices are appended (architecture rule 6)."""
        closes = np.array(wave_closes(160))
        full = generate_signals(strategy_type, parameters, closes)
        assert set(np.unique(full)) <= {-1, 0, 1}
        for cutoff in range(1, len(closes)):
            partial = generate_signals(strategy_type, parameters, closes[:cutoff])
            assert partial.tolist() == full[:cutoff].tolist(), f"look-ahead at bar {cutoff}"

    def test_wave_series_makes_active_strategies_trade(self):
        closes = np.array(wave_closes(160))
        for strategy_type in ("SMA_CROSSOVER", "MOMENTUM", "MEAN_REVERSION"):
            defaults = STRATEGY_REGISTRY[strategy_type].default_parameters()
            params = {**defaults, **({"lookback": 10} if "lookback" in defaults else {})}
            if strategy_type == "SMA_CROSSOVER":
                params = {"fast_window": 5, "slow_window": 20}
            signals = generate_signals(strategy_type, params, closes)
            assert np.count_nonzero(np.diff(signals)) >= 2, strategy_type


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class TestEngine:
    def test_buy_and_hold_without_costs(self):
        bars = make_bars([100, 110, 121])  # opens: 100, 100, 110
        result = run_backtest(bars, [1, 1, 1], config())

        # Signal from bar 0 fills at bar 1's open (100): 100 shares, no cash left.
        assert result.positions == [Decimal("0"), Decimal("100"), Decimal("100")]
        assert result.equity == [Decimal("10000"), Decimal("11000"), Decimal("12100")]
        assert result.metrics["total_return"] == pytest.approx(0.21)
        assert result.metrics["benchmark_buy_and_hold_return"] == pytest.approx(0.21)
        assert result.total_commission == 0

        (trade,) = result.trades
        assert trade.is_open
        assert trade.side == "LONG"
        assert trade.pnl == Decimal("2100")
        assert result.metrics["open_trades"] == 1
        assert result.metrics["closed_trades"] == 0

    def test_costs_are_charged_exactly(self):
        bars = make_bars([100, 110, 121])
        result = run_backtest(
            bars,
            [1, 0, 0],
            BacktestConfig(
                initial_capital=Decimal("10000"),
                commission_bps=Decimal("10"),
                slippage_bps=Decimal("50"),
            ),
        )
        # Entry at 100 * 1.005 = 100.5; 99 shares fit once 0.1% commission is included.
        # Exit at 110 * 0.995 = 109.45.
        (trade,) = result.trades
        assert trade.quantity == Decimal("99")
        assert trade.entry_price == Decimal("100.5")
        assert trade.exit_price == Decimal("109.45")
        assert trade.commission == Decimal("20.78505")
        assert trade.pnl == Decimal("865.26495")
        assert result.total_commission == Decimal("20.78505")
        assert result.total_slippage == Decimal("103.95")
        assert result.equity[-1] == Decimal("10865.26495")
        # Equity is fully explained by the trade: no cash is created or lost.
        assert result.equity[-1] - result.equity[0] == trade.pnl
        assert result.metrics["closed_trades"] == 1
        assert result.metrics["win_rate"] == 1.0

    def test_orders_fill_at_the_next_open_not_the_signal_bar(self):
        bars = make_bars([100] * 5, opens=[100, 100, 50, 100, 100])
        result = run_backtest(bars, [0, 1, 1, 0, 0], config())

        # Signal on bar 1 -> filled on bar 2 at its open (50). Signal off on bar 3 -> out on 4.
        assert [p != 0 for p in result.positions] == [False, False, True, True, False]
        (trade,) = result.trades
        assert trade.entry_price == Decimal("50")
        assert trade.entry_time == bars[2].timestamp
        assert trade.exit_time == bars[4].timestamp
        assert trade.exit_price == Decimal("100")

    def test_last_signal_is_never_traded(self):
        bars = make_bars([100, 100, 100])
        result = run_backtest(bars, [0, 0, 1], config())
        assert result.trades == []
        assert result.equity == [Decimal("10000")] * 3

    def test_short_signals_are_ignored_unless_enabled(self):
        bars = make_bars([100, 90, 80])
        long_only = run_backtest(bars, [-1, -1, -1], config())
        assert long_only.trades == []
        assert long_only.equity[-1] == Decimal("10000")

        short = run_backtest(bars, [-1, -1, -1], config(allow_short=True))
        assert short.positions[-1] == Decimal("-100")
        assert short.equity == [Decimal("10000"), Decimal("11000"), Decimal("12000")]
        assert short.trades[0].side == "SHORT"
        assert short.trades[0].pnl == Decimal("2000")

    def test_reversal_closes_then_opens(self):
        bars = make_bars([100, 100, 100, 100])
        result = run_backtest(bars, [1, -1, -1, -1], config(allow_short=True))
        assert [t.side for t in result.trades] == ["LONG", "SHORT"]
        assert result.trades[0].exit_time == result.trades[1].entry_time == bars[2].timestamp
        assert result.positions[-1] == Decimal("-100")

    def test_whole_versus_fractional_shares(self):
        bars = make_bars([300, 300, 300])
        whole = run_backtest(bars, [1, 1, 1], config("1000"))
        assert whole.trades[0].quantity == Decimal("3")

        fractional = run_backtest(bars, [1, 1, 1], config("1000", fractional_shares=True))
        assert fractional.trades[0].quantity == Decimal("3.33333333")

    def test_position_size_limits_capital_deployed(self):
        bars = make_bars([100, 100, 100])
        result = run_backtest(bars, [1, 1, 1], config(position_size=Decimal("0.25")))
        assert result.trades[0].quantity == Decimal("25")

    def test_capital_too_small_to_trade_warns_once(self):
        bars = make_bars([100] * 6)
        result = run_backtest(bars, [1] * 6, config("50"))
        assert result.trades == []
        assert len(result.warnings) == 1
        assert "entry skipped" in result.warnings[0]

    def test_short_squeeze_liquidates_and_halts(self):
        bars = make_bars([100, 100, 250, 100], opens=[100, 100, 100, 250])
        result = run_backtest(bars, [-1, -1, -1, -1], config(allow_short=True))
        assert result.positions[-2:] == [Decimal("0"), Decimal("0")]
        assert result.equity[-1] == result.equity[-2] == Decimal("-5000")
        assert any("liquidated" in warning for warning in result.warnings)
        assert len(result.trades) == 1

    def test_payload_is_json_serialisable_and_exact(self):
        import json

        closes = wave_closes(120)
        bars = make_bars(closes)
        signals = generate_signals("SMA_CROSSOVER", {"fast_window": 5, "slow_window": 20}, closes)
        payload = run_backtest(bars, signals, BacktestConfig(Decimal("100000"))).to_payload()

        json.dumps(payload, allow_nan=False)
        assert len(payload["equity_curve"]) == 120
        assert payload["final_equity"] == payload["equity_curve"][-1]["equity"]
        assert payload["metrics"]["closed_trades"] >= 1
        assert {"side", "pnl", "entry_time", "bars_held"} <= payload["trades"][0].keys()

    def test_is_deterministic(self):
        closes = wave_closes(200)
        bars = make_bars(closes)
        signals = generate_signals("MOMENTUM", {"lookback": 10}, closes)
        cfg = BacktestConfig(Decimal("50000"), allow_short=True)
        assert run_backtest(bars, signals, cfg).to_payload() == (
            run_backtest(bars, signals, cfg).to_payload()
        )

    @pytest.mark.parametrize(
        ("closes", "signals", "message"),
        [
            ([100], [1], "At least two bars"),
            ([100, 101], [1], "same length"),
        ],
    )
    def test_invalid_inputs(self, closes, signals, message):
        with pytest.raises(BacktestError, match=message):
            run_backtest(make_bars(closes), signals, config())

    def test_unsorted_bars_are_rejected(self):
        bars = make_bars([100, 101, 102])
        with pytest.raises(BacktestError, match="strictly increasing"):
            run_backtest([bars[1], bars[0], bars[2]], [1, 1, 1], config())

    @pytest.mark.parametrize(
        "overrides",
        [
            {"initial_capital": Decimal("0")},
            {"commission_bps": Decimal("-1")},
            {"position_size": Decimal("0")},
            {"position_size": Decimal("1.5")},
        ],
    )
    def test_invalid_config(self, overrides):
        with pytest.raises(BacktestError):
            BacktestConfig(**{"initial_capital": Decimal("1000"), **overrides})


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
class TestMetrics:
    def test_max_drawdown_and_duration(self):
        depth, duration = max_drawdown(np.array([100, 120, 90, 95, 130], dtype=float))
        assert depth == pytest.approx(-0.25)
        assert duration == 2

    def test_flat_curve_has_undefined_ratios(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        stamps = [start + timedelta(days=i) for i in range(10)]
        metrics = compute_performance_metrics(stamps, [100.0] * 10, periods_per_year=252)
        assert metrics["total_return"] == 0
        assert metrics["annualized_volatility"] == 0
        assert metrics["sharpe_ratio"] is None
        assert metrics["sortino_ratio"] is None
        assert metrics["calmar_ratio"] is None
        assert metrics["max_drawdown"] == 0
        assert metrics["win_rate"] is None

    def test_cagr_uses_elapsed_calendar_time(self):
        start = datetime(2020, 1, 1, tzinfo=UTC)
        stamps = [start, start + timedelta(days=731)]  # ~2 years
        metrics = compute_performance_metrics(stamps, [100.0, 121.0], periods_per_year=252)
        assert metrics["total_return"] == pytest.approx(0.21)
        assert metrics["cagr"] == pytest.approx(0.10, abs=1e-3)

    def test_sharpe_matches_definition(self):
        rng = np.random.default_rng(7)
        returns = rng.normal(0.001, 0.01, 500)
        equity = 100 * np.cumprod(np.concatenate(([1.0], 1 + returns)))
        start = datetime(2020, 1, 1, tzinfo=UTC)
        stamps = [start + timedelta(days=i) for i in range(len(equity))]

        metrics = compute_performance_metrics(
            stamps, equity.tolist(), periods_per_year=252, risk_free_rate=0.02
        )
        excess = returns - 0.02 / 252
        expected = excess.mean() / returns.std(ddof=1) * np.sqrt(252)
        assert metrics["sharpe_ratio"] == pytest.approx(expected, rel=1e-6)
        assert metrics["annualized_volatility"] == pytest.approx(
            returns.std(ddof=1) * np.sqrt(252), rel=1e-6
        )

    def test_trade_statistics(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        metrics = compute_performance_metrics(
            [start, start + timedelta(days=1)],
            [100.0, 110.0],
            periods_per_year=252,
            trade_pnls=[30.0, -10.0, 20.0, -10.0],
            trade_returns=[0.03, -0.01, 0.02, -0.01],
            bars_in_market=1,
        )
        assert metrics["closed_trades"] == 4
        assert metrics["win_rate"] == 0.5
        assert metrics["profit_factor"] == pytest.approx(2.5)
        assert metrics["average_trade_return"] == pytest.approx(0.0075)
        assert metrics["exposure"] == 0.5
