"""Schemas for portfolios, orders, trades and the ledger."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import DecimalStr
from app.trading.orders import OrderSide, OrderType

# NUMERIC(24, 8): sixteen integer digits, eight decimals.
PositiveAmount = Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=8)]


def _currency(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("must be a three-letter ISO 4217 currency code")
    return code


class PortfolioCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    base_currency: str = Field(..., min_length=3, max_length=3)
    initial_capital: PositiveAmount

    _normalize_currency = field_validator("base_currency")(_currency)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class PortfolioUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class PortfolioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    base_currency: str
    initial_capital: DecimalStr
    cash_balance: DecimalStr
    account_mode: str
    created_at: datetime
    updated_at: datetime


class CashMovementCreate(BaseModel):
    kind: Literal["DEPOSIT", "WITHDRAWAL"]
    amount: PositiveAmount
    description: str | None = Field(default=None, max_length=500)


class PositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    instrument_id: uuid.UUID
    symbol: str
    quantity: DecimalStr
    average_price: DecimalStr


class PositionValuationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    instrument_id: uuid.UUID
    symbol: str
    quantity: DecimalStr
    average_price: DecimalStr
    cost_basis: DecimalStr
    market_price: DecimalStr
    market_value: DecimalStr
    unrealized_pnl: DecimalStr
    price_as_of: datetime | None
    price_source: str


class PortfolioValuationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    portfolio_id: uuid.UUID
    currency_code: str
    cash: DecimalStr
    positions_value: DecimalStr
    total_equity: DecimalStr
    unrealized_pnl: DecimalStr
    valued_at: datetime
    positions: list[PositionValuationRead]
    warnings: list[str]


class EquitySnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cash: DecimalStr
    positions_value: DecimalStr
    total_equity: DecimalStr
    currency_code: str
    recorded_at: datetime


class OrderCreate(BaseModel):
    instrument_id: uuid.UUID
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    quantity: PositiveAmount
    limit_price: PositiveAmount | None = None

    @model_validator(mode="after")
    def limit_price_matches_type(self) -> "OrderCreate":
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("limit_price is only valid for LIMIT orders")
        return self


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    portfolio_id: uuid.UUID
    instrument_id: uuid.UUID
    side: str
    order_type: str
    quantity: DecimalStr
    filled_quantity: DecimalStr
    limit_price: DecimalStr | None
    status: str
    rejection_reason: str | None
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime


class OrderEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    from_status: str | None
    to_status: str
    reason: str | None
    created_at: datetime


class TradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    instrument_id: uuid.UUID
    side: str
    quantity: DecimalStr
    fill_price: DecimalStr
    commission: DecimalStr
    slippage: DecimalStr
    currency_code: str
    executed_at: datetime


class OrderDetailRead(OrderRead):
    events: list[OrderEventRead]
    trades: list[TradeRead]


class LedgerEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_number: int
    account: str
    instrument_id: uuid.UUID | None
    amount: DecimalStr


class LedgerTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    kind: str
    description: str | None
    trade_id: uuid.UUID | None
    currency_code: str
    occurred_at: datetime
    entries: list[LedgerEntryRead]


class LedgerBalancesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    balances: dict[str, DecimalStr]
    ledger_cash: DecimalStr
    portfolio_cash: DecimalStr
    cash_difference: DecimalStr
    ledger_securities_cost: DecimalStr
    positions_cost: DecimalStr
    securities_difference: DecimalStr
    is_balanced: bool
    is_reconciled: bool


class AllocationItem(BaseModel):
    strategy_id: uuid.UUID
    allocation_weight: Decimal = Field(..., ge=0, le=1, max_digits=12, decimal_places=8)


class AllocationsReplace(BaseModel):
    allocations: list[AllocationItem] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_strategies(self) -> "AllocationsReplace":
        ids = [item.strategy_id for item in self.allocations]
        if len(ids) != len(set(ids)):
            raise ValueError("Each strategy may appear only once")
        return self


class AllocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    strategy_id: uuid.UUID
    allocation_weight: DecimalStr
