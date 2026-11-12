# Entity Inventory

| Entity | Purpose |
|---|---|
| users | Account identity |
| exchanges | Exchange MIC, timezone, currency and calendar |
| instruments | Canonical listed asset identity |
| provider_symbols | Provider-specific ticker mapping |
| market_data_candles | Validated OHLCV bars per instrument, interval and adjustment mode |
| strategies | User-owned strategy identity and type |
| strategy_versions | Immutable parameter versions |
| experiments | Reproducible research runs with configuration, data fingerprint and results |
| simulation_jobs | Run state for an experiment |
| risk_reports | Stored VaR / expected-shortfall analysis of a portfolio or experiment |
| portfolios | User paper account: base currency, initial capital, cash |
| portfolio_allocations | Intended strategy weights |
| positions | Current holdings (quantity and average price) |
| orders | Submitted order intent and its current status |
| order_events | Append-only history of order status transitions |
| trades | Individual simulated fills |
| equity_snapshots | Historical account valuation |
| ledger_transactions | Header of one balanced double-entry posting |
| ledger_entries | Debit and credit lines of a ledger transaction |
