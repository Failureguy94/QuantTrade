from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Exchange, Instrument, User
from app.models.portfolio import (
    EquitySnapshot,
    Order,
    Portfolio,
    PortfolioAllocation,
    Position,
    Trade,
)
from app.models.research import Strategy


@pytest.mark.asyncio
async def test_financial_precision_portfolio_capital(db_session: AsyncSession):
    user = User(email="precision_user@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    # Large value with 8 decimal places: NUMERIC(24, 8)
    initial_cap = Decimal("1234567890123456.12345678")
    cash_bal = Decimal("987654321098765.87654321")

    portfolio = Portfolio(
        user_id=user.id,
        name="Precision Test Portfolio",
        base_currency="USD",
        initial_capital=initial_cap,
        cash_balance=cash_bal,
    )
    db_session.add(portfolio)
    await db_session.flush()

    # Re-fetch from DB
    stmt = select(Portfolio).where(Portfolio.id == portfolio.id)
    fetched = (await db_session.execute(stmt)).scalar_one()

    assert isinstance(fetched.initial_capital, Decimal)
    assert isinstance(fetched.cash_balance, Decimal)
    assert fetched.initial_capital == initial_cap
    assert fetched.cash_balance == cash_bal
    # Verify exact string representation without floating point imprecision
    assert str(fetched.initial_capital) == "1234567890123456.12345678"
    assert str(fetched.cash_balance) == "987654321098765.87654321"


@pytest.mark.asyncio
async def test_financial_precision_allocation_weight(db_session: AsyncSession):
    user = User(email="alloc_prec@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    portfolio = Portfolio(
        user_id=user.id,
        name="Alloc Precision Portfolio",
        base_currency="USD",
        initial_capital=Decimal("100000.00000000"),
        cash_balance=Decimal("100000.00000000"),
    )
    strategy = Strategy(
        user_id=user.id,
        name="Prec Strat",
        strategy_type="STAT_ARB",
    )
    db_session.add_all([portfolio, strategy])
    await db_session.flush()

    # Weight with 8 decimal places: NUMERIC(12, 8)
    weight = Decimal("0.33333333")
    alloc = PortfolioAllocation(
        portfolio_id=portfolio.id,
        strategy_id=strategy.id,
        allocation_weight=weight,
    )
    db_session.add(alloc)
    await db_session.flush()

    stmt = select(PortfolioAllocation).where(PortfolioAllocation.id == alloc.id)
    fetched = (await db_session.execute(stmt)).scalar_one()

    assert isinstance(fetched.allocation_weight, Decimal)
    assert fetched.allocation_weight == weight
    assert str(fetched.allocation_weight) == "0.33333333"


@pytest.mark.asyncio
async def test_financial_precision_position_and_average_price(db_session: AsyncSession):
    user = User(email="pos_prec@quant.example", password_hash="hashed")
    exchange = Exchange(
        mic="XNYS",
        name="NYSE",
        country_code="US",
        timezone="America/New_York",
        currency_code="USD",
        calendar_code="XNYS",
    )
    db_session.add_all([user, exchange])
    await db_session.flush()

    inst = Instrument(
        exchange_id=exchange.id,
        symbol="BRK.A",
        name="Berkshire Hathaway Inc.",
        currency_code="USD",
    )
    portfolio = Portfolio(
        user_id=user.id,
        name="High Net Worth Portfolio",
        base_currency="USD",
        initial_capital=Decimal("100000000.00000000"),
        cash_balance=Decimal("100000000.00000000"),
    )
    db_session.add_all([inst, portfolio])
    await db_session.flush()

    # Fractional shares with high precision + very high price
    fractional_qty = Decimal("0.00001234")
    high_avg_price = Decimal("685240.12500000")

    pos = Position(
        portfolio_id=portfolio.id,
        instrument_id=inst.id,
        quantity=fractional_qty,
        average_price=high_avg_price,
    )
    db_session.add(pos)
    await db_session.flush()

    stmt = select(Position).where(Position.id == pos.id)
    fetched = (await db_session.execute(stmt)).scalar_one()

    assert isinstance(fetched.quantity, Decimal)
    assert isinstance(fetched.average_price, Decimal)
    assert fetched.quantity == fractional_qty
    assert fetched.average_price == high_avg_price


@pytest.mark.asyncio
async def test_financial_precision_trades_commission_slippage(db_session: AsyncSession):
    user = User(email="trade_prec@quant.example", password_hash="hashed")
    exchange = Exchange(
        mic="XCBO",
        name="Cboe Options Exchange",
        country_code="US",
        timezone="America/Chicago",
        currency_code="USD",
        calendar_code="XCBO",
    )
    db_session.add_all([user, exchange])
    await db_session.flush()

    inst = Instrument(
        exchange_id=exchange.id,
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        currency_code="USD",
    )
    portfolio = Portfolio(
        user_id=user.id,
        name="Index Portfolio",
        base_currency="USD",
        initial_capital=Decimal("1000000.00000000"),
        cash_balance=Decimal("1000000.00000000"),
    )
    db_session.add_all([inst, portfolio])
    await db_session.flush()

    order = Order(
        portfolio_id=portfolio.id,
        instrument_id=inst.id,
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("1000.00000000"),
        filled_quantity=Decimal("1000.00000000"),
        limit_price=Decimal("545.12345678"),
        status="FILLED",
    )
    db_session.add(order)
    await db_session.flush()

    qty = Decimal("1000.00000000")
    fill_price = Decimal("545.12345678")
    comm = Decimal("0.00500000")  # half a cent commission per share
    slip = Decimal("0.00012345")  # sub-cent slippage

    trade = Trade(
        order_id=order.id,
        quantity=qty,
        fill_price=fill_price,
        commission=comm,
        slippage=slip,
        currency_code="USD",
        executed_at=datetime.now(UTC),
    )
    db_session.add(trade)
    await db_session.flush()

    stmt = select(Trade).where(Trade.id == trade.id)
    fetched = (await db_session.execute(stmt)).scalar_one()

    assert isinstance(fetched.quantity, Decimal)
    assert isinstance(fetched.fill_price, Decimal)
    assert isinstance(fetched.commission, Decimal)
    assert isinstance(fetched.slippage, Decimal)

    assert fetched.fill_price == fill_price
    assert fetched.commission == comm
    assert fetched.slippage == slip


@pytest.mark.asyncio
async def test_financial_precision_equity_snapshot(db_session: AsyncSession):
    user = User(email="snap_prec@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    portfolio = Portfolio(
        user_id=user.id,
        name="Snapshot Portfolio",
        base_currency="USD",
        initial_capital=Decimal("5000000.00000000"),
        cash_balance=Decimal("5000000.00000000"),
    )
    db_session.add(portfolio)
    await db_session.flush()

    cash = Decimal("2345678.12345678")
    pos_val = Decimal("8765432.87654321")
    total_eq = cash + pos_val  # 11111110.99999999

    snap = EquitySnapshot(
        portfolio_id=portfolio.id,
        cash=cash,
        positions_value=pos_val,
        total_equity=total_eq,
        currency_code="USD",
        recorded_at=datetime.now(UTC),
    )
    db_session.add(snap)
    await db_session.flush()

    stmt = select(EquitySnapshot).where(EquitySnapshot.id == snap.id)
    fetched = (await db_session.execute(stmt)).scalar_one()

    assert isinstance(fetched.cash, Decimal)
    assert isinstance(fetched.positions_value, Decimal)
    assert isinstance(fetched.total_equity, Decimal)

    assert fetched.cash == cash
    assert fetched.positions_value == pos_val
    assert fetched.total_equity == total_eq
    assert fetched.total_equity == Decimal("11111110.99999999")
