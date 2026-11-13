# Architecture Decisions

## ADR-001: Modular monolith

Keep business domains isolated in one deployable backend initially. Avoid premature
microservices. Extract workers or services only when workload or ownership requires it.

## ADR-002: Multi-market instrument identity

An instrument is identified internally by its UUID and linked to an exchange. Provider
symbols are separate records. Never use a raw Yahoo Finance ticker as the canonical ID.

## ADR-003: Currency and exchange metadata

Every instrument has a trading currency. Every exchange has a timezone, country,
calendar code and default currency. Portfolio base currency is independent of instrument
currency; FX conversion must be explicit and timestamped.

## ADR-004: Financial precision

Use PostgreSQL NUMERIC and Python Decimal for balances, quantities, fees and ledger
entries. Use NumPy floating-point for statistical computation and explicitly convert
validated results at the boundary.

## ADR-005: Provider abstraction

Strategies consume normalized OHLCV data. Provider-specific identifiers, response
formats, rate limits and errors stay inside adapters.

## ADR-006: Research versus execution

Backtesting and paper trading may share accounting and fill-model abstractions, but
paper accounts must be isolated from any future real brokerage integration.

## ADR-007: Reproducibility

Persist strategy version, configuration, data fingerprint, engine version, execution
assumptions and random seed for every experiment.

## ADR-008: Backtests run inside the request

A backtest on daily bars takes milliseconds once the data is cached, so it runs in the
request (CPU work on a worker thread) and the experiment is returned complete. A
`simulation_jobs` row still records each run, so moving long workloads to a queue
later changes where the work happens, not the data model. Run-time failures (no data,
provider outage) are stored on the experiment as `FAILED` rather than raised, so every
attempt is auditable.

## ADR-009: No look-ahead by construction

A strategy returns a target exposure per bar computed only from closes up to that bar.
The engine fills the resulting order at the next bar's open. The rule is enforced by a
test that checks no signal changes when later prices are appended.

## ADR-010: Double-entry ledger behind operational projections

`portfolios.cash_balance` and `positions` are fast-to-read projections. Every change
to them is posted in the same database transaction as one balanced ledger transaction
(debits positive, credits negative, sum zero). The ledger is the accounting record;
the projections are reconciled against it. Work for one portfolio is serialised with a
row lock on the portfolio, which also makes ledger sequence numbers gap-free.

## ADR-011: Paper fills use reference quotes that are never stored

A paper fill prices against the latest daily close from the provider, held briefly in
memory. Quotes are not written to the candle store, so a bar that is still forming
cannot reach backtest data. If the provider is down the newest stored candle is used,
and a price older than the configured limit is refused rather than traded on.

## ADR-012: Rejected orders are records, not errors

An order the paper broker refuses is persisted with status `REJECTED` and a reason,
and the submission still returns `201`. HTTP errors are reserved for requests that are
malformed or refer to something the caller cannot access. Order state changes go
through one transition table and are appended to `order_events`.

## ADR-013: Other users' resources look missing

Every query for a user-owned resource filters on the owner. A resource owned by
someone else returns `404`, identical to a missing one, so identifiers cannot be
probed. Idempotency keys are unique per portfolio for the same reason.

## ADR-014: Market data requires authentication

Every `/market-data` endpoint requires a signed-in user, declared once on the router so
a new endpoint in that module is protected by default. The historical endpoint can
trigger requests to the upstream provider; those must be attributable to an account and
cannot be available to anonymous callers. Authentication is checked before the query
string is validated, so an anonymous request learns nothing from a malformed one. The
plain `/instruments` list reads only local reference data and stays public.

## ADR-015: Deleting an account erases its data

`POST /auth/delete-account` removes the user and everything they own: strategies,
experiments, risk reports, portfolios, orders, trades and the ledger. This is paper
data with no regulatory retention duty, and a person must be able to leave completely.
Trades and ledger transactions are protected from accidental cascades by `RESTRICT`
foreign keys (see the database notes), so the service deletes the rows explicitly,
leaves first, in one transaction. The password is required again, so a stolen access
token alone cannot destroy an account.

## ADR-016: Request limits are counted in process memory

Rate limits, the failed-sign-in lockout and the cap on concurrent runs are counted in
the API process. This needs no extra infrastructure and is correct while the API runs
as one process, the same assumption the quote cache makes (ADR-011). The cost is that
counters reset on restart and are not shared between processes; running several API
processes means moving them to a shared store first. The lockout counts failures per
email address whether or not an account exists, so it cannot be used to discover which
addresses are registered.

## ADR-017: Rotating refresh tokens; the API holds no cookies

Access tokens are short-lived JWTs. Sessions are continued with opaque refresh tokens,
stored only as SHA-256 hashes, that are rotated on every use within a family. A retired
token presented again after a short grace period (which absorbs concurrent requests
from one client) means the token was copied, and the family is revoked. The API takes
and returns tokens in JSON bodies and sets no cookies, so it has no CSRF surface; the
web application keeps the refresh token in an httpOnly cookie on its own origin.
`users.tokens_valid_after` lets a password change or sign-out-everywhere refuse access
tokens that have not expired yet.
