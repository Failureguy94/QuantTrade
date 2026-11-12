"""Paper portfolios: funding, orders, positions, valuation and the ledger."""

import uuid

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_quote_service
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models.identity import Instrument, User
from app.models.portfolio import (
    EquitySnapshot,
    LedgerTransaction,
    Order,
    OrderEvent,
    Portfolio,
    PortfolioAllocation,
    Position,
    Trade,
)
from app.schemas.portfolio import (
    AllocationRead,
    AllocationsReplace,
    CashMovementCreate,
    EquitySnapshotRead,
    LedgerBalancesRead,
    LedgerTransactionRead,
    OrderCreate,
    OrderDetailRead,
    OrderEventRead,
    OrderRead,
    PortfolioCreate,
    PortfolioRead,
    PortfolioUpdate,
    PortfolioValuationRead,
    PositionRead,
    TradeRead,
)
from app.trading import portfolios as portfolio_service
from app.trading.ledger import LedgerKind, reconcile
from app.trading.orders import OrderRequest, OrderService
from app.trading.quotes import QuoteService

router = APIRouter()


def _trade_reads(rows) -> list[TradeRead]:
    return [
        TradeRead(
            id=trade.id,
            order_id=trade.order_id,
            instrument_id=instrument_id,
            side=side,
            quantity=trade.quantity,
            fill_price=trade.fill_price,
            commission=trade.commission,
            slippage=trade.slippage,
            currency_code=trade.currency_code,
            executed_at=trade.executed_at,
        )
        for trade, instrument_id, side in rows
    ]


# ------------------------------------------------------------------ portfolios
@router.post("", response_model=PortfolioRead, status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    payload: PortfolioCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Open a paper portfolio funded with ``initial_capital``."""
    portfolio = await portfolio_service.create_portfolio(
        db,
        user_id=current_user.id,
        name=payload.name,
        base_currency=payload.base_currency,
        initial_capital=payload.initial_capital,
    )
    await db.commit()
    await db.refresh(portfolio)
    return portfolio


@router.get("", response_model=list[PortfolioRead])
async def list_portfolios(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Portfolio)
        .where(Portfolio.user_id == current_user.id)
        .order_by(Portfolio.created_at.desc(), Portfolio.id)
        .limit(limit)
        .offset(offset)
    )
    return (await db.execute(stmt)).scalars().all()


@router.get("/{portfolio_id}", response_model=PortfolioRead)
async def get_portfolio(
    portfolio_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)


@router.patch("/{portfolio_id}", response_model=PortfolioRead)
async def rename_portfolio(
    portfolio_id: uuid.UUID,
    payload: PortfolioUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    portfolio.name = payload.name
    await db.commit()
    await db.refresh(portfolio)
    return portfolio


@router.post("/{portfolio_id}/cash", response_model=PortfolioRead)
async def move_cash(
    portfolio_id: uuid.UUID,
    payload: CashMovementCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deposit or withdraw paper cash. Recorded in the ledger as contributed capital."""
    portfolio = await portfolio_service.get_owned_portfolio(
        db, current_user.id, portfolio_id, for_update=True
    )
    await portfolio_service.move_cash(
        db,
        portfolio=portfolio,
        kind=LedgerKind(payload.kind),
        amount=payload.amount,
        description=payload.description,
    )
    await db.commit()
    await db.refresh(portfolio)
    return portfolio


# ------------------------------------------------------- positions & valuation
@router.get("/{portfolio_id}/positions", response_model=list[PositionRead])
async def list_positions(
    portfolio_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    stmt = (
        select(Position, Instrument.symbol)
        .join(Instrument, Instrument.id == Position.instrument_id)
        .where(Position.portfolio_id == portfolio.id)
        .order_by(Instrument.symbol)
    )
    return [
        PositionRead(
            instrument_id=position.instrument_id,
            symbol=symbol,
            quantity=position.quantity,
            average_price=position.average_price,
        )
        for position, symbol in (await db.execute(stmt)).all()
    ]


@router.get("/{portfolio_id}/valuation", response_model=PortfolioValuationRead)
async def get_valuation(
    portfolio_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    quotes: QuoteService = Depends(get_quote_service),
):
    """Mark the portfolio to market at the latest reference prices."""
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    return await portfolio_service.value_portfolio(db, portfolio, quotes)


@router.post(
    "/{portfolio_id}/snapshots",
    response_model=EquitySnapshotRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_equity_snapshot(
    portfolio_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    quotes: QuoteService = Depends(get_quote_service),
):
    """Value the portfolio now and store the result in its equity history."""
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    snapshot, _ = await portfolio_service.record_equity_snapshot(db, portfolio, quotes)
    await db.commit()
    return snapshot


@router.get("/{portfolio_id}/snapshots", response_model=list[EquitySnapshotRead])
async def list_equity_snapshots(
    portfolio_id: uuid.UUID,
    limit: int = Query(default=500, ge=1, le=5000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Equity history, oldest first (the most recent ``limit`` snapshots)."""
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    stmt = (
        select(EquitySnapshot)
        .where(EquitySnapshot.portfolio_id == portfolio.id)
        .order_by(EquitySnapshot.recorded_at.desc())
        .limit(limit)
    )
    return list(reversed((await db.execute(stmt)).scalars().all()))


# ---------------------------------------------------------------------- orders
@router.post("/{portfolio_id}/orders", response_model=OrderRead)
async def submit_order(
    portfolio_id: uuid.UUID,
    payload: OrderCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    quotes: QuoteService = Depends(get_quote_service),
):
    """Submit a paper order.

    Returns ``201`` for a new order and ``200`` when ``Idempotency-Key`` matched an
    earlier submission. An order the paper broker refuses (insufficient cash, no
    price, ...) is still created and returned with ``status: REJECTED`` and a
    ``rejection_reason``, so always inspect ``status``.
    """
    order, created = await OrderService(db, quotes).submit(
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        request=OrderRequest(
            instrument_id=payload.instrument_id,
            side=payload.side,
            order_type=payload.order_type,
            quantity=payload.quantity,
            limit_price=payload.limit_price,
        ),
        idempotency_key=idempotency_key,
    )
    await db.commit()
    await db.refresh(order)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return order


@router.get("/{portfolio_id}/orders", response_model=list[OrderRead])
async def list_orders(
    portfolio_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    stmt = (
        select(Order)
        .where(Order.portfolio_id == portfolio.id)
        .order_by(Order.created_at.desc(), Order.id)
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    if status_filter:
        stmt = stmt.where(Order.status == status_filter.upper())
    return (await db.execute(stmt)).scalars().all()


@router.post("/{portfolio_id}/orders/process", response_model=list[OrderRead])
async def process_open_orders(
    portfolio_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    quotes: QuoteService = Depends(get_quote_service),
):
    """Re-price resting limit orders against the latest quotes.

    Returns the orders whose status changed. Call this on whatever schedule suits
    the deployment; there is no background matcher.
    """
    orders = await OrderService(db, quotes).process_open_orders(
        user_id=current_user.id, portfolio_id=portfolio_id
    )
    await db.commit()
    for order in orders:
        await db.refresh(order)
    return orders


@router.get("/{portfolio_id}/orders/{order_id}", response_model=OrderDetailRead)
async def get_order(
    portfolio_id: uuid.UUID,
    order_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One order with its state-transition history and fills."""
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    order = (
        await db.execute(
            select(Order)
            .where(Order.id == order_id, Order.portfolio_id == portfolio.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("Order not found")

    events = (
        (
            await db.execute(
                select(OrderEvent)
                .where(OrderEvent.order_id == order.id)
                .order_by(OrderEvent.sequence)
            )
        )
        .scalars()
        .all()
    )
    trades = (
        (
            await db.execute(
                select(Trade).where(Trade.order_id == order.id).order_by(Trade.executed_at)
            )
        )
        .scalars()
        .all()
    )
    return OrderDetailRead(
        **OrderRead.model_validate(order).model_dump(),
        events=[OrderEventRead.model_validate(event) for event in events],
        trades=_trade_reads((trade, order.instrument_id, order.side) for trade in trades),
    )


@router.post("/{portfolio_id}/orders/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(
    portfolio_id: uuid.UUID,
    order_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    quotes: QuoteService = Depends(get_quote_service),
):
    """Cancel a resting order. Orders that already reached a final state return 409."""
    order = await OrderService(db, quotes).cancel(
        user_id=current_user.id, portfolio_id=portfolio_id, order_id=order_id
    )
    await db.commit()
    await db.refresh(order)
    return order


@router.get("/{portfolio_id}/trades", response_model=list[TradeRead])
async def list_trades(
    portfolio_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fills for the portfolio, newest first."""
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    stmt = (
        select(Trade, Order.instrument_id, Order.side)
        .join(Order, Order.id == Trade.order_id)
        .where(Order.portfolio_id == portfolio.id)
        .order_by(Trade.executed_at.desc(), Trade.id)
        .limit(limit)
        .offset(offset)
    )
    return _trade_reads((await db.execute(stmt)).all())


# ---------------------------------------------------------------------- ledger
@router.get("/{portfolio_id}/ledger", response_model=list[LedgerTransactionRead])
async def list_ledger_transactions(
    portfolio_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Double-entry journal, newest first. Debits are positive, credits negative."""
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    stmt = (
        select(LedgerTransaction)
        .options(selectinload(LedgerTransaction.entries))
        .where(LedgerTransaction.portfolio_id == portfolio.id)
        .order_by(LedgerTransaction.sequence.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await db.execute(stmt)).scalars().all()


@router.get("/{portfolio_id}/ledger/balances", response_model=LedgerBalancesRead)
async def get_ledger_balances(
    portfolio_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Account balances and a reconciliation against cash and positions."""
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    return await reconcile(db, portfolio)


# ----------------------------------------------------------------- allocations
@router.get("/{portfolio_id}/allocations", response_model=list[AllocationRead])
async def list_allocations(
    portfolio_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await portfolio_service.get_owned_portfolio(db, current_user.id, portfolio_id)
    stmt = (
        select(PortfolioAllocation)
        .where(PortfolioAllocation.portfolio_id == portfolio.id)
        .order_by(PortfolioAllocation.allocation_weight.desc(), PortfolioAllocation.strategy_id)
    )
    return (await db.execute(stmt)).scalars().all()


@router.put("/{portfolio_id}/allocations", response_model=list[AllocationRead])
async def replace_allocations(
    portfolio_id: uuid.UUID,
    payload: AllocationsReplace,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace the portfolio's strategy weights. Weights may sum to at most 1.

    Allocations record intent; they do not place orders.
    """
    portfolio = await portfolio_service.get_owned_portfolio(
        db, current_user.id, portfolio_id, for_update=True
    )
    allocations = await portfolio_service.replace_allocations(
        db,
        portfolio=portfolio,
        user_id=current_user.id,
        weights={item.strategy_id: item.allocation_weight for item in payload.allocations},
    )
    await db.commit()
    return sorted(allocations, key=lambda a: (-a.allocation_weight, str(a.strategy_id)))
