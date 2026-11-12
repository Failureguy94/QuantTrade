# Database Notes

The SQLAlchemy models are the schema source of truth; Alembic migrations are generated
from them and reviewed by hand.

## Main domains

- Identity/reference data: users, exchanges, instruments, provider_symbols.
- Market data: market_data_candles.
- Research: strategies, strategy_versions, experiments, simulation_jobs, risk_reports.
- Portfolio/execution: portfolios, portfolio_allocations, positions, orders,
  order_events, trades, equity_snapshots.
- Accounting: ledger_transactions, ledger_entries.

## Invariants the schema enforces

- Candles: positive prices, `low <= open, close <= high`, non-negative volume, one row
  per (instrument, interval, adjustment mode, timestamp).
- Orders: positive quantity, `0 <= filled_quantity <= quantity`, idempotency key unique
  per portfolio.
- Trades: positive quantity and fill price.
- Positions: one per (portfolio, instrument), never zero quantity (a closed position
  is deleted).
- Strategy versions: version number unique per strategy.
- Order events and ledger transactions: sequence number unique per order / portfolio.
- Ledger entries: non-zero amount. That the entries of a transaction sum to zero is
  enforced in `app/trading/ledger.py`, not by a constraint.

## Deletion behaviour

Trades reference orders with `ON DELETE RESTRICT`, and ledger transactions reference
trades the same way, so a portfolio that has traded cannot be deleted by cascade. This
is intentional: there is no portfolio delete endpoint. Deleting an experiment keeps
its job records and risk reports and clears their link to it.

## Done since the initial schema

1. Double-entry portfolio ledger (`ledger_transactions`, `ledger_entries`).
2. Order event history with transactionally enforced state transitions
   (`order_events`).
3. Ownership checks on every user-owned resource query.
4. Experiment results, start and completion timestamps.

## Still open before production

1. Currency/FX rates with source and observation timestamp. Until then a portfolio
   trades only instruments in its base currency.
2. Exchange sessions and complete holiday calendars (India covers fixed holidays only).
3. Market-data dataset/version metadata and data-quality audit records beyond the
   per-request fingerprint.
4. Strategy-to-portfolio execution bindings and account-level risk limits.
   `portfolio_allocations` records intended weights but nothing trades on them.
5. Corporate actions on paper positions (splits, dividends).
6. Retention and partitioning policies, once data volume has been measured.

## Important

Do not use this schema to process real money.
