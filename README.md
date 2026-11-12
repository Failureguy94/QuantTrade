# Quant Backend

[![CI](https://github.com/Failureguy94/QuantTrade/actions/workflows/ci.yml/badge.svg)](https://github.com/Failureguy94/QuantTrade/actions/workflows/ci.yml)

FastAPI + SQLAlchemy 2.0 async + PostgreSQL backend for quantitative research,
backtesting, portfolio risk and paper trading.

## What it does

- **Reference data** for multiple markets: exchanges, instruments and provider symbols.
- **Market data**: a provider interface with a Yahoo Finance adapter, strict OHLCV
  validation, a candle store and a SHA-256 fingerprint of every dataset served.
- **Authentication**: Argon2id password hashing and JWT bearer tokens.
- **Strategies**: a catalogue of built-in strategy types, with user strategies stored as
  immutable parameter versions.
- **Backtesting**: a bar-driven engine with next-open fills, commission and slippage,
  optional shorting, and performance metrics. Every run is stored as a reproducible
  experiment.
- **Paper trading**: market and limit orders, an order state machine with event
  history, idempotent submission, positions and cash.
- **Accounting**: a double-entry ledger behind every cash and position change, with a
  reconciliation endpoint.
- **Risk**: historical, parametric and Monte Carlo Value-at-Risk and expected
  shortfall for portfolios and backtests.

Every user-owned resource is scoped to its owner. A resource that belongs to someone
else is reported as `404`, the same as one that does not exist.

### Limits to know about

This is a research and paper-trading system. It does not connect to a broker and must
not be used to handle real money.

- **Single currency per portfolio.** An order for an instrument in another currency is
  rejected; FX conversion is not implemented.
- **Long-only paper trading.** Sells are limited to the quantity held. (Backtests can
  short, without borrow cost or margin.)
- **Paper fills are simplified**: the full quantity fills at the latest daily reference
  price, adjusted for slippage. There is no order book, partial fill or volume limit.
- **Backtests run inside the request**, on one instrument, on daily, weekly or monthly
  bars.
- **Resting limit orders are re-priced on demand** through
  `POST /portfolios/{id}/orders/process`; there is no background matcher.
- **Yahoo Finance is a research feed**, not real-time or exchange-grade, and its
  licensing must be checked before production use. The India calendars cover only
  fixed national holidays (see `app/market_data/calendar.py`).

## Quick start with Docker

```bash
docker compose up --build
```

This starts PostgreSQL, applies the migrations, loads the reference data and serves
the API on http://localhost:8000 (Swagger UI at `/docs`). Settings are read from
`.env` if it exists. The stack uses development credentials and publishes the API on
localhost only.

## Requirements

- Python 3.12+
- Docker, or a local PostgreSQL 16 instance

## Local setup

To run the API from a virtualenv, with only the database in Docker:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
cp .env.example .env             # PowerShell: Copy-Item .env.example .env
docker compose up -d postgres

alembic upgrade head             # create the schema
python -m app.cli seed           # load exchanges and instruments (safe to re-run)
```

Set a real `JWT_SECRET` in `.env` before exposing the API. In production
(`APP_ENV=production`) the application refuses to start with the default or a secret
shorter than 32 characters.

## Run

```bash
uvicorn app.main:app --reload
```

- Swagger UI: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health

## Walkthrough

```bash
API=http://localhost:8000/api/v1

# 1. Register and log in
curl -s -X POST $API/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"trader@quant.example","password":"SecurePassword123!"}'
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"trader@quant.example","password":"SecurePassword123!"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

# 2. Find an instrument
AAPL=$(curl -s "$API/market-data/instruments?search=AAPL" -H "$AUTH" \
  | python -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')

# 3. Create a strategy (this also creates version 1 of its parameters)
VERSION=$(curl -s -X POST $API/strategies -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"SMA 20/50","strategy_type":"SMA_CROSSOVER","parameters":{"fast_window":20,"slow_window":50}}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["latest_version"]["id"])')

# 4. Backtest it
curl -s -X POST $API/experiments/backtests -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"strategy_version_id\":\"$VERSION\",\"instrument_id\":\"$AAPL\",\"start\":\"2022-01-01\",\"end\":\"2024-12-31\"}"

# 5. Open a paper portfolio and buy
PORTFOLIO=$(curl -s -X POST $API/portfolios -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"Paper","base_currency":"USD","initial_capital":"100000"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s -X POST $API/portfolios/$PORTFOLIO/orders -H "$AUTH" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: my-first-order' \
  -d "{\"instrument_id\":\"$AAPL\",\"side\":\"BUY\",\"quantity\":\"50\"}"

# 6. Value it and measure its risk
curl -s $API/portfolios/$PORTFOLIO/valuation -H "$AUTH"
curl -s -X POST $API/risk/reports -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"portfolio_id\":\"$PORTFOLIO\",\"method\":\"HISTORICAL\",\"confidence_level\":0.95}"
```

## API

All paths are under `/api/v1`. Health, registration, login and the plain
`/instruments` list are public; everything else, including every `/market-data`
endpoint, requires `Authorization: Bearer <token>`. Monetary values are exact decimal strings; statistics
are JSON numbers.

### Auth, health and reference data

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register` | Create a user (email is lower-cased; password ≥ 8 characters) |
| POST | `/auth/login` | Exchange credentials for a JWT access token |
| GET | `/auth/me` | Current user |
| GET | `/health`, `/health/database` | Service and database health |
| GET | `/instruments` | Active instruments |
| GET | `/market-data/instruments` | Instruments with exchange and provider symbols |
| GET | `/market-data/providers` | Registered providers and their capabilities |
| GET | `/market-data/{instrument_id}/historical` | Validated OHLCV bars with a data fingerprint |

### Strategies

| Method | Path | Purpose |
|---|---|---|
| GET | `/strategies/types` | Built-in types with parameter schemas and defaults |
| POST | `/strategies` | Create a strategy and version 1 |
| GET | `/strategies` | List (`include_archived`, `limit`, `offset`) |
| GET, PATCH | `/strategies/{id}` | Read; rename, re-describe or archive |
| POST, GET | `/strategies/{id}/versions` | Append a parameter version; list versions |

Built-in types: `BUY_AND_HOLD`, `SMA_CROSSOVER` (`fast_window`, `slow_window`),
`MOMENTUM` (`lookback`, `threshold`), `MEAN_REVERSION` (`lookback`, `entry_z`,
`exit_z`). Versions are never edited: changing parameters means adding a version, so
an experiment always points at exactly what it ran.

### Experiments (backtests)

| Method | Path | Purpose |
|---|---|---|
| POST | `/experiments/backtests` | Run a backtest and store it |
| GET | `/experiments` | List with metrics (`status`, `strategy_version_id`) |
| GET, DELETE | `/experiments/{id}` | Full results; delete |
| GET | `/experiments/{id}/jobs` | Job records for the run |

Request fields: `strategy_version_id`, `instrument_id`, `start`, `end`, and optionally
`interval` (`1d`, `1wk`, `1mo`), `adjusted`, `initial_capital`, `commission_bps`,
`slippage_bps`, `position_size`, `allow_short`, `fractional_shares`, `risk_free_rate`.

The response is `201` whenever the experiment was recorded. Check `status`:
`COMPLETED` carries `results` (metrics, equity curve, trades); `FAILED` carries
`error_message`, for example when the provider has no data for the range.

If the candle store holds only part of the requested range, the range is fetched
again rather than backtesting on a partial cache. If the provider's own data starts
late or ends early, the run completes and `results.warnings` states what was covered.
Portfolio risk reports apply the same check and carry the same `warnings`.

How a backtest trades: the signal for a bar uses closes up to and including that bar,
and the order fills at the **next bar's open**, so a strategy cannot act on information
it would not have had. The stored experiment records the strategy version and
parameters, the full request, the engine version and the fingerprint of the bars used.
Re-running the same request on the same data gives the same result.

### Portfolios and paper trading

| Method | Path | Purpose |
|---|---|---|
| POST, GET | `/portfolios` | Open a paper portfolio; list |
| GET, PATCH | `/portfolios/{id}` | Read; rename |
| POST | `/portfolios/{id}/cash` | Deposit or withdraw |
| GET | `/portfolios/{id}/positions` | Holdings |
| GET | `/portfolios/{id}/valuation` | Mark to market at the latest reference prices |
| POST, GET | `/portfolios/{id}/snapshots` | Record / list equity history |
| POST, GET | `/portfolios/{id}/orders` | Submit an order; list (`status`) |
| POST | `/portfolios/{id}/orders/process` | Re-price resting limit orders |
| GET | `/portfolios/{id}/orders/{order_id}` | Order with its event history and fills |
| POST | `/portfolios/{id}/orders/{order_id}/cancel` | Cancel a resting order |
| GET | `/portfolios/{id}/trades` | Fills |
| GET | `/portfolios/{id}/ledger` | Double-entry journal |
| GET | `/portfolios/{id}/ledger/balances` | Account balances and reconciliation |
| GET, PUT | `/portfolios/{id}/allocations` | Strategy weights (recorded intent; places no orders) |

**Orders.** `side` is `BUY` or `SELL`; `order_type` is `MARKET` (default) or `LIMIT`
with a `limit_price`. Submitting returns `201` with the order. An order the paper
broker refuses — not enough cash, nothing to sell, no usable price, wrong currency —
is still created, with `status: REJECTED` and a `rejection_reason`, so always read
`status`. A limit order that is not yet marketable is `OPEN` until it is filled by
`/orders/process` or cancelled.

**Idempotency.** Send an `Idempotency-Key` header to make a submission safe to retry.
Repeating the same request with the same key returns the original order with `200`;
reusing a key for a different order returns `409`. Keys are scoped to the portfolio.

**Fill model.** The reference price is the close of the most recent daily bar from
the provider (cached for `QUOTE_CACHE_TTL_SECONDS`). If the provider is unreachable,
the newest stored daily candle is used instead, and if the newest price is older than
`QUOTE_MAX_AGE_DAYS` the order is rejected. Buys fill at the reference price plus
`PAPER_SLIPPAGE_BPS`, sells at the reference minus it, never beyond a limit price;
`PAPER_COMMISSION_BPS` of notional is charged.

**Ledger.** Each deposit, withdrawal and fill posts one balanced transaction (debits
positive, credits negative, summing to zero) across `CASH`, `SECURITIES`,
`CONTRIBUTED_CAPITAL`, `REALIZED_PNL` and `FEES`. `/ledger/balances` compares the
ledger with the portfolio's cash and positions and reports `is_reconciled`.

### Risk

| Method | Path | Purpose |
|---|---|---|
| POST | `/risk/reports` | Compute and store a report for a portfolio or an experiment |
| GET | `/risk/reports` | List (`portfolio_id`, `experiment_id`) |
| GET | `/risk/reports/{id}` | Read |

Give exactly one of `portfolio_id` or `experiment_id`, plus optionally `method`
(`HISTORICAL`, `PARAMETRIC`, `MONTE_CARLO`), `confidence_level`, `horizon_days`,
`lookback_days`, `simulations` and `random_seed`.

Value-at-Risk and expected shortfall are reported as positive fractions of portfolio
value and as currency amounts. A portfolio report measures the *current* holdings
over the last `lookback_days` sessions; an experiment report uses the returns of the
backtest's equity curve. Horizons longer than one period use square-root-of-time
scaling for the historical and parametric methods and are simulated directly by Monte
Carlo. The Monte Carlo seed is stored with the report (generated if you do not supply
one), so a report can be reproduced.

## Configuration

Set in `.env` (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local Postgres on 5433 | Async SQLAlchemy URL |
| `JWT_SECRET` | development placeholder | Token signing key (≥ 32 characters in production) |
| `JWT_ALGORITHM` | `HS256` | Signing algorithm |
| `ACCESS_TOKEN_MINUTES` | `30` | Access token lifetime |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed browser origins |
| `PAPER_COMMISSION_BPS` | `5` | Paper commission, basis points of notional |
| `PAPER_SLIPPAGE_BPS` | `2` | Paper slippage, basis points of price |
| `QUOTE_CACHE_TTL_SECONDS` | `60` | How long a reference quote is reused |
| `QUOTE_MAX_AGE_DAYS` | `7` | Oldest price an order may fill against |

## Development

```bash
pytest                  # needs the Postgres container; uses a throwaway schema
ruff check app tests
mypy app
```

Tests never call Yahoo Finance: the provider is replaced by an in-memory fake.

GitHub Actions (`.github/workflows/ci.yml`) runs the same checks on every push and
pull request, applies the migrations to an empty database, and builds and starts the
Docker stack.

After changing a model, generate and review a migration:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

Autogenerate does not detect `CHECK` constraints added to existing tables; add those
to the migration by hand.

## Layout

```
app/
  api/            HTTP layer: dependencies, error mapping, v1 endpoints
  backtesting/    strategy catalogue, engine, metrics, experiment service
  core/           settings, security, domain errors, decimal helpers
  db/             engine, session, declarative base
  market_data/    provider interface, Yahoo adapter, validator, calendars, repository
  models/         SQLAlchemy models (the schema source of truth)
  risk/           VaR / expected-shortfall estimators and report service
  schemas/        Pydantic request and response models
  trading/        orders, quotes, portfolio accounting, double-entry ledger
  cli.py          operational commands (seed)
Dockerfile        API image (used by docker-compose.yml)
docs/             architecture decisions, database notes, entity inventory
migrations/       Alembic migrations
tests/
```

## Architecture rules

1. Strategies consume normalized market data, never provider-specific payloads.
2. Provider symbols are separate from internal instrument identifiers.
3. Exchange timezone, calendar and currency are explicit reference data.
4. Store financial ledger values using PostgreSQL NUMERIC / Python Decimal.
5. Use NumPy floating point for statistical computation, with explicit conversion at
   financial ledger boundaries.
6. Never use future observations when generating historical signals.
7. Paper trading must remain isolated from real brokerage execution.

See `docs/architecture-decisions.md` for the reasoning behind these and the later
decisions.

## Licence

MIT. See `LICENSE`.
