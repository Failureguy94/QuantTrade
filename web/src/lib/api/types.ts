import type { components } from "./schema";

type Schemas = components["schemas"];

export type User = Schemas["UserRead"];
export type Instrument = Schemas["MarketDataInstrumentRead"];
export type HistoricalData = Schemas["HistoricalDataResponse"];
export type Candle = Schemas["HistoricalCandleRead"];
export type StrategyType = Schemas["StrategyTypeRead"];
export type Strategy = Schemas["StrategyRead"];
export type StrategyVersion = Schemas["StrategyVersionRead"];
export type BacktestRequest = Schemas["BacktestRequest"];
export type ExperimentSummary = Schemas["ExperimentSummary"];
export type Experiment = Schemas["ExperimentRead"];
export type Portfolio = Schemas["PortfolioRead"];
export type Valuation = Schemas["PortfolioValuationRead"];
export type Position = Schemas["PositionRead"];
export type Order = Schemas["OrderRead"];
export type OrderDetail = Schemas["OrderDetailRead"];
export type Trade = Schemas["TradeRead"];
export type LedgerTransaction = Schemas["LedgerTransactionRead"];
export type LedgerBalances = Schemas["LedgerBalancesRead"];
export type EquitySnapshot = Schemas["EquitySnapshotRead"];
export type RiskReport = Schemas["RiskReportRead"];
export type RiskReportCreate = Schemas["RiskReportCreate"];

/** The API stores results as JSON; these are the shapes it writes. */

export type BacktestMetrics = {
  bars: number;
  total_return: number | null;
  cagr: number | null;
  annualized_volatility: number | null;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  max_drawdown: number | null;
  max_drawdown_duration_bars: number | null;
  calmar_ratio: number | null;
  closed_trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  average_trade_return: number | null;
  exposure: number | null;
  benchmark_buy_and_hold_return: number | null;
};

export type EquityPoint = { timestamp: string; equity: string; position: string };

export type BacktestTrade = {
  side: string;
  quantity: string;
  entry_time: string;
  entry_price: string;
  exit_time: string | null;
  exit_price: string | null;
  bars_held: number;
  commission: string;
  pnl: string;
  return_pct: number;
  is_open: boolean;
};

export type BacktestResults = {
  metrics: BacktestMetrics;
  final_equity: string;
  total_commission: string;
  total_slippage: string;
  equity_curve: EquityPoint[];
  trades: BacktestTrade[];
  warnings: string[];
  data: { bars: number; first_bar: string; last_bar: string; provider: string; currency: string };
};

export type BacktestConfiguration = {
  request: BacktestRequest;
  strategy: { id: string; type: string; version: number; parameters: Record<string, unknown> };
  instrument: { id: string; symbol: string; currency: string };
  execution: { fill: string; adjustment_mode: string };
};

export type RiskResults = {
  subject: "PORTFOLIO" | "EXPERIMENT";
  method: string;
  confidence_level: number;
  horizon_periods: number;
  observations: number;
  value_at_risk: number;
  expected_shortfall: number;
  value_at_risk_amount: string;
  expected_shortfall_amount: string;
  portfolio_value: string;
  annualized_volatility: number | null;
  mean_period_return: number;
  worst_period_return: number;
  best_period_return: number;
  max_drawdown: number;
  currency?: string;
  as_of: string;
  cash_weight?: number;
  positions?: { instrument_id: string; symbol: string; weight: number; market_value: string }[];
  warnings?: string[];
};

export function backtestResults(experiment: Experiment): BacktestResults | null {
  return (experiment.results as BacktestResults | null | undefined) ?? null;
}

export function backtestConfiguration(
  experiment: Pick<ExperimentSummary, "configuration">,
): BacktestConfiguration {
  return experiment.configuration as unknown as BacktestConfiguration;
}

export function riskResults(report: RiskReport): RiskResults {
  return report.results as unknown as RiskResults;
}
