import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Portfolio(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "portfolios"
    __table_args__ = (Index("ix_portfolios_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    account_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="PAPER")


class PortfolioAllocation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "portfolio_allocations"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "strategy_id", name="uq_portfolio_strategy"),
        CheckConstraint("allocation_weight >= 0 AND allocation_weight <= 1",
                        name="ck_allocation_weight_range"),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False
    )
    allocation_weight: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)


class Position(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "instrument_id", name="uq_position_portfolio_instrument"),
        CheckConstraint("quantity <> 0", name="ck_position_nonzero_quantity"),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    average_price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)


class Order(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_portfolio_created", "portfolio_id", "created_at"),
        Index("ix_orders_status", "status"),
        CheckConstraint("quantity > 0", name="ck_order_positive_quantity"),
        CheckConstraint(
            "filled_quantity >= 0 AND filled_quantity <= quantity",
            name="ck_order_filled_within_quantity",
        ),
        # Idempotency keys are scoped to a portfolio so that one account's keys can
        # never collide with (or reveal the existence of) another account's orders.
        UniqueConstraint("portfolio_id", "idempotency_key", name="uq_orders_portfolio_idempotency"),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class OrderEvent(UUIDPrimaryKey, Base):
    """Append-only history of order state transitions."""

    __tablename__ = "order_events"
    __table_args__ = (
        UniqueConstraint("order_id", "sequence", name="uq_order_events_order_sequence"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Trade(UUIDPrimaryKey, Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_order_executed", "order_id", "executed_at"),
        CheckConstraint("quantity > 0", name="ck_trade_positive_quantity"),
        CheckConstraint("fill_price > 0", name="ck_trade_positive_fill_price"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    slippage: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EquitySnapshot(UUIDPrimaryKey, Base):
    __tablename__ = "equity_snapshots"
    __table_args__ = (Index("ix_equity_portfolio_recorded", "portfolio_id", "recorded_at"),)

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    positions_value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LedgerTransaction(UUIDPrimaryKey, Base):
    """
    Header of one balanced double-entry posting against a portfolio.

    Every transaction owns two or more ``LedgerEntry`` rows whose signed amounts
    (debits positive, credits negative) sum to exactly zero.
    """

    __tablename__ = "ledger_transactions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "sequence", name="uq_ledger_tx_portfolio_sequence"),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trades.id", ondelete="RESTRICT")
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entries: Mapped[list["LedgerEntry"]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="LedgerEntry.line_number",
    )


class LedgerEntry(UUIDPrimaryKey, Base):
    """One debit (amount > 0) or credit (amount < 0) line of a ledger transaction."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index("ix_ledger_entries_portfolio_account", "portfolio_id", "account"),
        Index("ix_ledger_entries_transaction", "transaction_id"),
        CheckConstraint("amount <> 0", name="ck_ledger_entry_nonzero_amount"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(nullable=False)
    account: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instruments.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)

    transaction: Mapped[LedgerTransaction] = relationship(back_populates="entries")
