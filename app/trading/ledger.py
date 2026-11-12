"""Double-entry portfolio ledger.

Every cash or position change is posted as one balanced transaction: debits are
positive amounts, credits negative, and the entries of a transaction sum to
exactly zero. ``Portfolio.cash_balance`` and ``Position`` rows are operational
projections; this ledger is the record they are reconciled against.

Accounts
--------
``CASH``                 asset   — money available to trade
``SECURITIES``           asset   — holdings at cost, tagged with the instrument
``CONTRIBUTED_CAPITAL``  equity  — deposits less withdrawals (credit balance)
``REALIZED_PNL``         income  — gains are credits, losses debits
``FEES``                 expense — commissions paid

Callers must hold the portfolio row lock (``SELECT ... FOR UPDATE``) while
posting so per-portfolio sequence numbers are gap-free and unique.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, quantize
from app.models.portfolio import LedgerEntry, LedgerTransaction, Portfolio, Position

# Position cost is stored as quantity x average price, each rounded to eight
# places, so it can differ from the ledger's exact cost by rounding dust.
SECURITIES_TOLERANCE = Decimal("0.01")


class LedgerAccount(StrEnum):
    CASH = "CASH"
    SECURITIES = "SECURITIES"
    CONTRIBUTED_CAPITAL = "CONTRIBUTED_CAPITAL"
    REALIZED_PNL = "REALIZED_PNL"
    FEES = "FEES"


class LedgerKind(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRADE_BUY = "TRADE_BUY"
    TRADE_SELL = "TRADE_SELL"


class LedgerImbalanceError(RuntimeError):
    """A posting did not balance. This is a programming error, never user input."""


@dataclass(frozen=True)
class Posting:
    account: LedgerAccount
    amount: Decimal
    instrument_id: uuid.UUID | None = None


@dataclass(frozen=True)
class LedgerReconciliation:
    balances: dict[str, Decimal]
    ledger_cash: Decimal
    portfolio_cash: Decimal
    cash_difference: Decimal
    ledger_securities_cost: Decimal
    positions_cost: Decimal
    securities_difference: Decimal
    is_balanced: bool
    is_reconciled: bool


async def post_transaction(
    db: AsyncSession,
    *,
    portfolio: Portfolio,
    kind: LedgerKind,
    postings: Sequence[Posting],
    occurred_at: datetime,
    description: str | None = None,
    trade_id: uuid.UUID | None = None,
) -> LedgerTransaction:
    """Post one balanced transaction. Zero-amount lines are dropped."""
    lines = [posting for posting in postings if posting.amount != 0]
    total = sum((posting.amount for posting in lines), ZERO)
    if total != 0:
        raise LedgerImbalanceError(
            f"{kind.value} posting for portfolio {portfolio.id} is out of balance by {total}"
        )
    if len(lines) < 2:
        raise LedgerImbalanceError(f"{kind.value} posting needs at least two non-zero lines")

    last_sequence = await db.scalar(
        select(func.coalesce(func.max(LedgerTransaction.sequence), 0)).where(
            LedgerTransaction.portfolio_id == portfolio.id
        )
    )
    transaction = LedgerTransaction(
        portfolio_id=portfolio.id,
        sequence=int(last_sequence or 0) + 1,
        kind=kind.value,
        description=description,
        trade_id=trade_id,
        currency_code=portfolio.base_currency,
        occurred_at=occurred_at,
        entries=[
            LedgerEntry(
                portfolio_id=portfolio.id,
                line_number=line_number,
                account=posting.account.value,
                instrument_id=posting.instrument_id,
                amount=posting.amount,
            )
            for line_number, posting in enumerate(lines, start=1)
        ],
    )
    db.add(transaction)
    await db.flush()
    return transaction


async def account_balances(db: AsyncSession, portfolio_id: uuid.UUID) -> dict[str, Decimal]:
    """Signed balance per account (debit-positive). Accounts never posted to are zero."""
    stmt = (
        select(LedgerEntry.account, func.sum(LedgerEntry.amount))
        .where(LedgerEntry.portfolio_id == portfolio_id)
        .group_by(LedgerEntry.account)
    )
    balances = {account.value: quantize(ZERO) for account in LedgerAccount}
    for account, amount in (await db.execute(stmt)).all():
        balances[account] = quantize(amount)
    return balances


async def securities_cost(
    db: AsyncSession, portfolio_id: uuid.UUID, instrument_id: uuid.UUID
) -> Decimal:
    """Cost of the holding in one instrument according to the ledger."""
    amount = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.portfolio_id == portfolio_id,
            LedgerEntry.account == LedgerAccount.SECURITIES.value,
            LedgerEntry.instrument_id == instrument_id,
        )
    )
    return Decimal(amount or 0)


async def reconcile(db: AsyncSession, portfolio: Portfolio) -> LedgerReconciliation:
    """Compare the ledger with the portfolio's cash and position projections."""
    balances = await account_balances(db, portfolio.id)
    positions_cost = await db.scalar(
        select(func.coalesce(func.sum(Position.quantity * Position.average_price), 0)).where(
            Position.portfolio_id == portfolio.id
        )
    )
    positions_cost = quantize(Decimal(positions_cost or 0))
    ledger_cash = balances[LedgerAccount.CASH.value]
    ledger_securities = balances[LedgerAccount.SECURITIES.value]
    cash_difference = quantize(portfolio.cash_balance - ledger_cash)
    securities_difference = quantize(positions_cost - ledger_securities)
    return LedgerReconciliation(
        balances=balances,
        ledger_cash=ledger_cash,
        portfolio_cash=portfolio.cash_balance,
        cash_difference=cash_difference,
        ledger_securities_cost=ledger_securities,
        positions_cost=positions_cost,
        securities_difference=securities_difference,
        is_balanced=sum(balances.values(), ZERO) == 0,
        is_reconciled=(
            cash_difference == 0 and abs(securities_difference) <= SECURITIES_TOLERANCE
        ),
    )
