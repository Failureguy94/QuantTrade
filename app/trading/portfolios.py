"""Portfolio accounting workflows: funding, valuation, snapshots, allocations."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableError
from app.core.money import MAX_LEDGER_VALUE, ZERO, quantize
from app.models.identity import Instrument
from app.models.portfolio import EquitySnapshot, Portfolio, PortfolioAllocation, Position
from app.models.research import Strategy
from app.trading.ledger import LedgerAccount, LedgerKind, Posting, post_transaction
from app.trading.quotes import QuoteService, QuoteUnavailableError

ACCOUNT_MODE_PAPER = "PAPER"


@dataclass(frozen=True)
class PositionValuation:
    instrument_id: uuid.UUID
    symbol: str
    quantity: Decimal
    average_price: Decimal
    cost_basis: Decimal
    market_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    price_as_of: datetime | None
    price_source: str


@dataclass(frozen=True)
class PortfolioValuation:
    portfolio_id: uuid.UUID
    currency_code: str
    cash: Decimal
    positions_value: Decimal
    total_equity: Decimal
    unrealized_pnl: Decimal
    valued_at: datetime
    positions: list[PositionValuation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def get_owned_portfolio(
    db: AsyncSession,
    user_id: uuid.UUID,
    portfolio_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Portfolio:
    """Load a portfolio the user owns. Someone else's portfolio is reported as missing."""
    stmt = select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    if for_update:
        # populate_existing: the row lock is only useful if we also see the
        # balance committed by whoever held the lock before us.
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    portfolio = (await db.execute(stmt)).scalar_one_or_none()
    if portfolio is None:
        raise NotFoundError("Portfolio not found")
    return portfolio


async def create_portfolio(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    base_currency: str,
    initial_capital: Decimal,
) -> Portfolio:
    """Open a paper portfolio and record its opening deposit in the ledger."""
    capital = quantize(initial_capital)
    portfolio = Portfolio(
        user_id=user_id,
        name=name,
        base_currency=base_currency.upper(),
        initial_capital=capital,
        cash_balance=capital,
        account_mode=ACCOUNT_MODE_PAPER,
    )
    db.add(portfolio)
    await db.flush()
    await post_transaction(
        db,
        portfolio=portfolio,
        kind=LedgerKind.DEPOSIT,
        postings=[
            Posting(LedgerAccount.CASH, capital),
            Posting(LedgerAccount.CONTRIBUTED_CAPITAL, -capital),
        ],
        occurred_at=datetime.now(UTC),
        description="Initial capital",
    )
    return portfolio


async def move_cash(
    db: AsyncSession,
    *,
    portfolio: Portfolio,
    kind: LedgerKind,
    amount: Decimal,
    description: str | None = None,
) -> Portfolio:
    """Deposit to or withdraw from a portfolio. ``portfolio`` must be locked for update."""
    if kind not in (LedgerKind.DEPOSIT, LedgerKind.WITHDRAWAL):
        raise ValueError(f"{kind} is not a cash movement")
    value = quantize(amount)
    if value <= 0:
        raise UnprocessableError("Amount must be positive")

    signed = value if kind is LedgerKind.DEPOSIT else -value
    if portfolio.cash_balance + signed < 0:
        raise UnprocessableError(
            f"Insufficient cash: {portfolio.cash_balance} available, {value} requested"
        )
    if portfolio.cash_balance + signed > MAX_LEDGER_VALUE:
        raise UnprocessableError("Deposit would exceed the maximum representable balance")
    portfolio.cash_balance += signed
    await post_transaction(
        db,
        portfolio=portfolio,
        kind=kind,
        postings=[
            Posting(LedgerAccount.CASH, signed),
            Posting(LedgerAccount.CONTRIBUTED_CAPITAL, -signed),
        ],
        occurred_at=datetime.now(UTC),
        description=description or kind.value.title(),
    )
    return portfolio


async def value_portfolio(
    db: AsyncSession, portfolio: Portfolio, quotes: QuoteService
) -> PortfolioValuation:
    """Mark the portfolio to market.

    A position whose instrument has no usable quote is carried at cost and
    reported in ``warnings`` rather than failing the whole valuation.
    """
    stmt = (
        select(Position, Instrument.symbol)
        .join(Instrument, Instrument.id == Position.instrument_id)
        .where(Position.portfolio_id == portfolio.id)
        .order_by(Instrument.symbol)
    )
    valuations: list[PositionValuation] = []
    warnings: list[str] = []
    positions_value = ZERO
    unrealized = ZERO

    for position, symbol in (await db.execute(stmt)).all():
        cost_basis = quantize(position.quantity * position.average_price)
        try:
            quote = await quotes.get_quote(position.instrument_id)
            price, as_of, source = quote.price, quote.as_of, quote.source
        except QuoteUnavailableError as exc:
            price, as_of, source = position.average_price, None, "COST"
            warnings.append(f"{symbol}: valued at cost ({exc})")
        market_value = quantize(position.quantity * price)
        pnl = market_value - cost_basis
        positions_value += market_value
        unrealized += pnl
        valuations.append(
            PositionValuation(
                instrument_id=position.instrument_id,
                symbol=symbol,
                quantity=position.quantity,
                average_price=position.average_price,
                cost_basis=cost_basis,
                market_price=price,
                market_value=market_value,
                unrealized_pnl=pnl,
                price_as_of=as_of,
                price_source=source,
            )
        )

    return PortfolioValuation(
        portfolio_id=portfolio.id,
        currency_code=portfolio.base_currency,
        cash=portfolio.cash_balance,
        positions_value=positions_value,
        total_equity=portfolio.cash_balance + positions_value,
        unrealized_pnl=unrealized,
        valued_at=datetime.now(UTC),
        positions=valuations,
        warnings=warnings,
    )


async def record_equity_snapshot(
    db: AsyncSession, portfolio: Portfolio, quotes: QuoteService
) -> tuple[EquitySnapshot, PortfolioValuation]:
    valuation = await value_portfolio(db, portfolio, quotes)
    snapshot = EquitySnapshot(
        portfolio_id=portfolio.id,
        cash=valuation.cash,
        positions_value=valuation.positions_value,
        total_equity=valuation.total_equity,
        currency_code=valuation.currency_code,
        recorded_at=valuation.valued_at,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot, valuation


async def replace_allocations(
    db: AsyncSession,
    *,
    portfolio: Portfolio,
    user_id: uuid.UUID,
    weights: dict[uuid.UUID, Decimal],
) -> list[PortfolioAllocation]:
    """Replace the portfolio's strategy weights with ``weights`` (strategy id -> weight)."""
    if sum(weights.values(), ZERO) > 1:
        raise UnprocessableError("Allocation weights must not sum to more than 1")

    if weights:
        owned = set(
            (
                await db.execute(
                    select(Strategy.id).where(
                        Strategy.id.in_(weights), Strategy.user_id == user_id
                    )
                )
            ).scalars()
        )
        missing = sorted(str(strategy_id) for strategy_id in set(weights) - owned)
        if missing:
            raise NotFoundError(f"Strategy not found: {', '.join(missing)}")

    await db.execute(
        delete(PortfolioAllocation).where(PortfolioAllocation.portfolio_id == portfolio.id)
    )
    allocations = [
        PortfolioAllocation(
            portfolio_id=portfolio.id, strategy_id=strategy_id, allocation_weight=weight
        )
        for strategy_id, weight in weights.items()
    ]
    db.add_all(allocations)
    await db.flush()
    return allocations
