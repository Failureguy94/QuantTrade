"""Paper order execution.

Scope (ADR-006): only ``PAPER`` portfolios can be traded, and nothing here talks
to a broker. Orders fill in full against a reference quote, moved against the
trader by the configured slippage and charged the configured commission.

Order lifecycle::

    PENDING ─┬─> FILLED
             ├─> REJECTED
             └─> OPEN ─┬─> FILLED
                       ├─> CANCELLED
                       └─> REJECTED   (funds or holdings gone by the time it is marketable)

Every transition is validated against ``ALLOWED_TRANSITIONS`` and appended to
``order_events``. All work for one portfolio is serialised by locking its row.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.money import bps_to_rate, quantize
from app.models.identity import Instrument
from app.models.portfolio import Order, OrderEvent, Portfolio, Position, Trade
from app.trading.ledger import (
    LedgerAccount,
    LedgerKind,
    Posting,
    post_transaction,
    securities_cost,
)
from app.trading.portfolios import ACCOUNT_MODE_PAPER, get_owned_portfolio
from app.trading.quotes import Quote, QuoteService, QuoteUnavailableError


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


# PARTIALLY_FILLED is part of the model for future fill models; the paper fill
# model always fills an order in full.
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset(
        {OrderStatus.OPEN, OrderStatus.FILLED, OrderStatus.REJECTED}
    ),
    OrderStatus.OPEN: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset({OrderStatus.FILLED, OrderStatus.CANCELLED}),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}

CANCELLABLE = frozenset({OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED})


class InvalidOrderTransitionError(RuntimeError):
    """Code attempted a transition the state machine forbids."""


@dataclass(frozen=True)
class OrderRequest:
    instrument_id: uuid.UUID
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None


class OrderService:
    def __init__(
        self,
        db: AsyncSession,
        quotes: QuoteService,
        *,
        commission_bps: Decimal | None = None,
        slippage_bps: Decimal | None = None,
    ) -> None:
        self.db = db
        self.quotes = quotes
        self.commission_rate = bps_to_rate(
            settings.paper_commission_bps if commission_bps is None else commission_bps
        )
        self.slippage_rate = bps_to_rate(
            settings.paper_slippage_bps if slippage_bps is None else slippage_bps
        )

    # ------------------------------------------------------------------ state
    async def _transition(
        self, order: Order, to_status: OrderStatus, reason: str | None = None
    ) -> None:
        current = OrderStatus(order.status)
        if to_status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidOrderTransitionError(
                f"Order {order.id} cannot move from {current.value} to {to_status.value}"
            )
        order.status = to_status.value
        if to_status is OrderStatus.REJECTED:
            order.rejection_reason = reason
        await self._record_event(order, current, to_status, reason)

    async def _record_event(
        self,
        order: Order,
        from_status: OrderStatus | None,
        to_status: OrderStatus,
        reason: str | None,
    ) -> None:
        last_sequence = await self.db.scalar(
            select(func.coalesce(func.max(OrderEvent.sequence), 0)).where(
                OrderEvent.order_id == order.id
            )
        )
        self.db.add(
            OrderEvent(
                order_id=order.id,
                sequence=int(last_sequence or 0) + 1,
                from_status=from_status.value if from_status else None,
                to_status=to_status.value,
                reason=reason,
            )
        )
        await self.db.flush()

    # ----------------------------------------------------------------- submit
    async def submit(
        self,
        *,
        user_id: uuid.UUID,
        portfolio_id: uuid.UUID,
        request: OrderRequest,
        idempotency_key: str | None = None,
    ) -> tuple[Order, bool]:
        """Submit an order. Returns ``(order, created)``.

        ``created`` is False when ``idempotency_key`` matched an earlier
        submission, in which case that order is returned untouched.
        """
        portfolio = await get_owned_portfolio(
            self.db, user_id, portfolio_id, for_update=True
        )
        if portfolio.account_mode != ACCOUNT_MODE_PAPER:
            raise UnprocessableError("Only paper portfolios can be traded")

        if idempotency_key is not None:
            existing = (
                await self.db.execute(
                    select(Order).where(
                        Order.portfolio_id == portfolio.id,
                        Order.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if not self._same_request(existing, request):
                    raise ConflictError(
                        "Idempotency-Key was already used for a different order"
                    )
                return existing, False

        instrument = await self.db.get(Instrument, request.instrument_id)
        if instrument is None:
            raise NotFoundError("Instrument not found")

        order = Order(
            portfolio_id=portfolio.id,
            instrument_id=instrument.id,
            side=request.side.value,
            order_type=request.order_type.value,
            quantity=request.quantity,
            filled_quantity=Decimal("0"),
            limit_price=request.limit_price,
            status=OrderStatus.PENDING.value,
            idempotency_key=idempotency_key,
        )
        self.db.add(order)
        await self.db.flush()
        await self._record_event(order, None, OrderStatus.PENDING, "Order received")

        problem = self._pre_trade_check(portfolio, instrument)
        if problem is not None:
            await self._transition(order, OrderStatus.REJECTED, problem)
            return order, True

        try:
            quote = await self.quotes.get_quote(instrument.id)
        except QuoteUnavailableError as exc:
            await self._transition(order, OrderStatus.REJECTED, f"No usable price: {exc}")
            return order, True

        await self._execute(order, portfolio, instrument, quote)
        return order, True

    @staticmethod
    def _same_request(order: Order, request: OrderRequest) -> bool:
        return (
            order.instrument_id == request.instrument_id
            and order.side == request.side.value
            and order.order_type == request.order_type.value
            and order.quantity == request.quantity
            and order.limit_price == request.limit_price
        )

    @staticmethod
    def _pre_trade_check(portfolio: Portfolio, instrument: Instrument) -> str | None:
        if not instrument.is_active:
            return "Instrument is not active"
        if instrument.currency_code != portfolio.base_currency:
            return (
                f"Instrument trades in {instrument.currency_code} but the portfolio is in "
                f"{portfolio.base_currency}; cross-currency orders are not supported"
            )
        return None

    # ---------------------------------------------------------------- execute
    def _fill_price(self, order: Order, quote: Quote) -> Decimal | None:
        """Price the order would fill at now, or None if a limit is not marketable."""
        buying = order.side == OrderSide.BUY.value
        slipped = quantize(
            quote.price * (1 + self.slippage_rate if buying else 1 - self.slippage_rate)
        )
        if order.order_type == OrderType.MARKET.value:
            return slipped
        limit = order.limit_price
        if limit is None:
            raise InvalidOrderTransitionError(f"Limit order {order.id} has no limit price")
        if buying:
            return min(slipped, limit) if quote.price <= limit else None
        return max(slipped, limit) if quote.price >= limit else None

    async def _execute(
        self, order: Order, portfolio: Portfolio, instrument: Instrument, quote: Quote
    ) -> None:
        price = self._fill_price(order, quote)
        if price is None:
            if order.status == OrderStatus.PENDING.value:
                await self._transition(
                    order,
                    OrderStatus.OPEN,
                    f"Resting: reference price {quote.price} is outside the limit",
                )
            return

        quantity = order.quantity - order.filled_quantity
        notional = quantize(quantity * price)
        commission = quantize(notional * self.commission_rate)
        position = (
            await self.db.execute(
                select(Position)
                .where(
                    Position.portfolio_id == portfolio.id,
                    Position.instrument_id == instrument.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

        if order.side == OrderSide.BUY.value:
            required = notional + commission
            if required > portfolio.cash_balance:
                await self._transition(
                    order,
                    OrderStatus.REJECTED,
                    f"Insufficient cash: {required} required, "
                    f"{portfolio.cash_balance} available",
                )
                return
        else:
            held = position.quantity if position is not None else Decimal("0")
            if quantity > held:
                await self._transition(
                    order,
                    OrderStatus.REJECTED,
                    f"Insufficient holdings: selling {quantity}, {held} held "
                    "(short selling is not supported)",
                )
                return

        executed_at = datetime.now(UTC)
        trade = Trade(
            order_id=order.id,
            quantity=quantity,
            fill_price=price,
            commission=commission,
            slippage=quantize(quantity * abs(price - quote.price)),
            currency_code=instrument.currency_code,
            executed_at=executed_at,
        )
        self.db.add(trade)
        await self.db.flush()

        if order.side == OrderSide.BUY.value:
            await self._book_buy(portfolio, instrument, position, trade, notional)
        else:
            assert position is not None  # guaranteed by the holdings check above
            await self._book_sell(portfolio, instrument, position, trade, notional)

        order.filled_quantity = order.quantity
        await self._transition(
            order,
            OrderStatus.FILLED,
            f"Filled {quantity} @ {price} (reference {quote.price} from {quote.source})",
        )

    async def _book_buy(
        self,
        portfolio: Portfolio,
        instrument: Instrument,
        position: Position | None,
        trade: Trade,
        notional: Decimal,
    ) -> None:
        if position is None:
            self.db.add(
                Position(
                    portfolio_id=portfolio.id,
                    instrument_id=instrument.id,
                    quantity=trade.quantity,
                    average_price=trade.fill_price,
                )
            )
        else:
            new_quantity = position.quantity + trade.quantity
            position.average_price = quantize(
                (position.quantity * position.average_price + notional) / new_quantity
            )
            position.quantity = new_quantity

        total = notional + trade.commission
        portfolio.cash_balance -= total
        await post_transaction(
            self.db,
            portfolio=portfolio,
            kind=LedgerKind.TRADE_BUY,
            postings=[
                Posting(LedgerAccount.SECURITIES, notional, instrument.id),
                Posting(LedgerAccount.FEES, trade.commission),
                Posting(LedgerAccount.CASH, -total),
            ],
            occurred_at=trade.executed_at,
            description=f"Buy {trade.quantity} {instrument.symbol} @ {trade.fill_price}",
            trade_id=trade.id,
        )

    async def _book_sell(
        self,
        portfolio: Portfolio,
        instrument: Instrument,
        position: Position,
        trade: Trade,
        notional: Decimal,
    ) -> None:
        if trade.quantity == position.quantity:
            # Closing out: release the ledger's exact remaining cost so the
            # SECURITIES balance for this instrument returns to zero.
            ledger_cost = await securities_cost(self.db, portfolio.id, instrument.id)
            cost_basis = (
                ledger_cost
                if ledger_cost > 0
                else quantize(trade.quantity * position.average_price)
            )
            await self.db.delete(position)
        else:
            cost_basis = quantize(trade.quantity * position.average_price)
            position.quantity -= trade.quantity

        proceeds = notional - trade.commission
        portfolio.cash_balance += proceeds
        await post_transaction(
            self.db,
            portfolio=portfolio,
            kind=LedgerKind.TRADE_SELL,
            postings=[
                Posting(LedgerAccount.CASH, proceeds),
                Posting(LedgerAccount.FEES, trade.commission),
                Posting(LedgerAccount.SECURITIES, -cost_basis, instrument.id),
                Posting(LedgerAccount.REALIZED_PNL, cost_basis - notional),
            ],
            occurred_at=trade.executed_at,
            description=f"Sell {trade.quantity} {instrument.symbol} @ {trade.fill_price}",
            trade_id=trade.id,
        )

    # ------------------------------------------------------- cancel / process
    async def _get_order(self, portfolio: Portfolio, order_id: uuid.UUID) -> Order:
        order = (
            await self.db.execute(
                select(Order)
                .where(Order.id == order_id, Order.portfolio_id == portfolio.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found")
        return order

    async def cancel(
        self, *, user_id: uuid.UUID, portfolio_id: uuid.UUID, order_id: uuid.UUID
    ) -> Order:
        portfolio = await get_owned_portfolio(
            self.db, user_id, portfolio_id, for_update=True
        )
        order = await self._get_order(portfolio, order_id)
        if OrderStatus(order.status) not in CANCELLABLE:
            raise ConflictError(f"Order is {order.status} and cannot be cancelled")
        await self._transition(order, OrderStatus.CANCELLED, "Cancelled by user")
        return order

    async def process_open_orders(
        self, *, user_id: uuid.UUID, portfolio_id: uuid.UUID
    ) -> list[Order]:
        """Re-price resting limit orders, oldest first. Returns the orders that changed."""
        portfolio = await get_owned_portfolio(
            self.db, user_id, portfolio_id, for_update=True
        )
        open_orders = (
            (
                await self.db.execute(
                    select(Order)
                    .where(
                        Order.portfolio_id == portfolio.id,
                        Order.status == OrderStatus.OPEN.value,
                    )
                    .order_by(Order.created_at, Order.id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )

        changed: list[Order] = []
        for order in open_orders:
            instrument = await self.db.get(Instrument, order.instrument_id)
            if instrument is None:
                continue
            try:
                quote = await self.quotes.get_quote(instrument.id)
            except QuoteUnavailableError:
                continue  # stays open until a price is available
            await self._execute(order, portfolio, instrument, quote)
            if order.status != OrderStatus.OPEN.value:
                changed.append(order)
        return changed
