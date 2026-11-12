import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Exchange, Instrument, ProviderSymbol, User
from app.models.portfolio import (
    Order,
    Portfolio,
    PortfolioAllocation,
    Position,
    Trade,
)
from app.models.research import (
    Experiment,
    RiskReport,
    SimulationJob,
    Strategy,
    StrategyVersion,
)


@pytest.mark.asyncio
async def test_user_creation_and_retrieval(db_session: AsyncSession):
    user = User(
        email="trader@quant.example",
        password_hash="argon2_hashed_secret",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    assert user.id is not None
    assert user.created_at is not None
    assert user.updated_at is not None

    stmt = select(User).where(User.email == "trader@quant.example")
    fetched = (await db_session.execute(stmt)).scalar_one()
    assert fetched.id == user.id
    assert fetched.is_active is True


@pytest.mark.asyncio
async def test_user_duplicate_email_rejection(db_session: AsyncSession):
    u1 = User(email="unique@quant.example", password_hash="hash1")
    db_session.add(u1)
    await db_session.flush()

    u2 = User(email="unique@quant.example", password_hash="hash2")
    db_session.add(u2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_exchange_and_instrument_creation(db_session: AsyncSession):
    from sqlalchemy.orm import selectinload

    exchange = Exchange(
        mic="XNAS",
        name="NASDAQ Stock Market",
        country_code="US",
        timezone="America/New_York",
        currency_code="USD",
        calendar_code="XNAS",
        is_active=True,
    )
    db_session.add(exchange)
    await db_session.flush()

    instrument = Instrument(
        exchange_id=exchange.id,
        symbol="AAPL",
        name="Apple Inc.",
        asset_type="EQUITY",
        currency_code="USD",
        isin="US0378331005",
        is_active=True,
    )
    db_session.add(instrument)
    await db_session.flush()

    assert instrument.id is not None

    stmt = (
        select(Exchange)
        .where(Exchange.id == exchange.id)
        .options(selectinload(Exchange.instruments))
    )
    loaded_exchange = (await db_session.execute(stmt)).scalar_one()
    assert len(loaded_exchange.instruments) == 1
    assert loaded_exchange.instruments[0].symbol == "AAPL"


@pytest.mark.asyncio
async def test_duplicate_exchange_mic_rejection(db_session: AsyncSession):
    ex1 = Exchange(
        mic="XNYS",
        name="New York Stock Exchange",
        country_code="US",
        timezone="America/New_York",
        currency_code="USD",
        calendar_code="XNYS",
    )
    db_session.add(ex1)
    await db_session.flush()

    ex2 = Exchange(
        mic="XNYS",
        name="Duplicate NYSE",
        country_code="US",
        timezone="America/New_York",
        currency_code="USD",
        calendar_code="XNYS",
    )
    db_session.add(ex2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_duplicate_instrument_symbol_within_exchange_rejection(db_session: AsyncSession):
    exchange = Exchange(
        mic="XLON",
        name="London Stock Exchange",
        country_code="GB",
        timezone="Europe/London",
        currency_code="GBP",
        calendar_code="XLON",
    )
    db_session.add(exchange)
    await db_session.flush()

    inst1 = Instrument(
        exchange_id=exchange.id,
        symbol="VOD",
        name="Vodafone Group",
        currency_code="GBP",
    )
    db_session.add(inst1)
    await db_session.flush()

    inst2 = Instrument(
        exchange_id=exchange.id,
        symbol="VOD",
        name="Vodafone Duplicate",
        currency_code="GBP",
    )
    db_session.add(inst2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_provider_symbol_relationship_and_cascade(db_session: AsyncSession):
    exchange = Exchange(
        mic="XTSE",
        name="Toronto Stock Exchange",
        country_code="CA",
        timezone="America/Toronto",
        currency_code="CAD",
        calendar_code="XTSE",
    )
    db_session.add(exchange)
    await db_session.flush()

    inst = Instrument(
        exchange_id=exchange.id,
        symbol="SHOP",
        name="Shopify Inc.",
        currency_code="CAD",
    )
    db_session.add(inst)
    await db_session.flush()

    ps = ProviderSymbol(
        instrument_id=inst.id,
        provider="YAHOO",
        provider_symbol="SHOP.TO",
        is_primary=True,
    )
    db_session.add(ps)
    await db_session.flush()

    from sqlalchemy.orm import selectinload

    stmt = (
        select(Instrument)
        .where(Instrument.id == inst.id)
        .options(selectinload(Instrument.provider_symbols))
    )
    loaded_inst = (await db_session.execute(stmt)).scalar_one()
    assert len(loaded_inst.provider_symbols) == 1
    assert loaded_inst.provider_symbols[0].provider_symbol == "SHOP.TO"

    # Test duplicate provider symbol rejection
    ps_dup = ProviderSymbol(
        instrument_id=inst.id,
        provider="YAHOO",
        provider_symbol="SHOP.TO",
    )
    db_session.add(ps_dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_strategy_and_strategy_version_relationship(db_session: AsyncSession):
    user = User(email="strat_dev@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    strategy = Strategy(
        user_id=user.id,
        name="Statistical Arbitrage Mean Reversion",
        strategy_type="STAT_ARB",
        description="Cointegration pairs trading with Kalman filter hedge ratio",
    )
    db_session.add(strategy)
    await db_session.flush()

    v1 = StrategyVersion(
        strategy_id=strategy.id,
        version=1,
        parameters={"lookback": 60, "entry_z": 2.0, "exit_z": 0.5},
        code_version="git_sha_abc123",
    )
    db_session.add(v1)
    await db_session.flush()

    assert v1.id is not None
    assert v1.strategy_id == strategy.id
    assert v1.parameters["entry_z"] == 2.0


@pytest.mark.asyncio
async def test_portfolio_creation_and_ownership(db_session: AsyncSession):
    user = User(email="owner@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    portfolio = Portfolio(
        user_id=user.id,
        name="Alpha Live Portfolio",
        base_currency="USD",
        initial_capital=Decimal("1000000.00000000"),
        cash_balance=Decimal("1000000.00000000"),
        account_mode="PAPER",
    )
    db_session.add(portfolio)
    await db_session.flush()

    assert portfolio.id is not None
    assert portfolio.user_id == user.id
    assert portfolio.initial_capital == Decimal("1000000.00000000")


@pytest.mark.asyncio
async def test_portfolio_allocation_weight_and_constraints(db_session: AsyncSession):
    user = User(email="alloc_user@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    portfolio = Portfolio(
        user_id=user.id,
        name="Multi-Strat Portfolio",
        base_currency="USD",
        initial_capital=Decimal("500000.00000000"),
        cash_balance=Decimal("500000.00000000"),
    )
    strategy = Strategy(
        user_id=user.id,
        name="Trend Momentum",
        strategy_type="MOMENTUM",
    )
    db_session.add_all([portfolio, strategy])
    await db_session.flush()

    # Valid allocation
    alloc = PortfolioAllocation(
        portfolio_id=portfolio.id,
        strategy_id=strategy.id,
        allocation_weight=Decimal("0.75000000"),
    )
    db_session.add(alloc)
    await db_session.flush()
    assert alloc.id is not None

    # Invalid allocation weight > 1.0 violates ck_allocation_weight_range
    invalid_alloc = PortfolioAllocation(
        portfolio_id=portfolio.id,
        strategy_id=strategy.id,
        allocation_weight=Decimal("1.25000000"),
    )
    db_session.add(invalid_alloc)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_portfolio_allocation_negative_weight_rejection(db_session: AsyncSession):
    user = User(email="neg_alloc@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    portfolio = Portfolio(
        user_id=user.id,
        name="Portfolio P1",
        base_currency="USD",
        initial_capital=Decimal("10000.00000000"),
        cash_balance=Decimal("10000.00000000"),
    )
    strategy = Strategy(
        user_id=user.id,
        name="Strat S1",
        strategy_type="TREND",
    )
    db_session.add_all([portfolio, strategy])
    await db_session.flush()

    invalid_alloc = PortfolioAllocation(
        portfolio_id=portfolio.id,
        strategy_id=strategy.id,
        allocation_weight=Decimal("-0.10000000"),
    )
    db_session.add(invalid_alloc)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_position_order_and_trade_relationships(db_session: AsyncSession):
    user = User(email="trader2@quant.example", password_hash="hashed")
    exchange = Exchange(
        mic="XAMS",
        name="Euronext Amsterdam",
        country_code="NL",
        timezone="Europe/Amsterdam",
        currency_code="EUR",
        calendar_code="XAMS",
    )
    db_session.add_all([user, exchange])
    await db_session.flush()

    inst = Instrument(
        exchange_id=exchange.id,
        symbol="ASML",
        name="ASML Holding NV",
        currency_code="EUR",
    )
    portfolio = Portfolio(
        user_id=user.id,
        name="Tech Fund",
        base_currency="EUR",
        initial_capital=Decimal("200000.00000000"),
        cash_balance=Decimal("200000.00000000"),
    )
    db_session.add_all([inst, portfolio])
    await db_session.flush()

    # Position
    pos = Position(
        portfolio_id=portfolio.id,
        instrument_id=inst.id,
        quantity=Decimal("150.00000000"),
        average_price=Decimal("650.50000000"),
    )
    db_session.add(pos)
    await db_session.flush()
    assert pos.id is not None

    # Order
    order = Order(
        portfolio_id=portfolio.id,
        instrument_id=inst.id,
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("50.00000000"),
        filled_quantity=Decimal("50.00000000"),
        limit_price=Decimal("649.00000000"),
        status="FILLED",
        idempotency_key="order-asml-001",
    )
    db_session.add(order)
    await db_session.flush()
    assert order.id is not None

    # Trade
    trade = Trade(
        order_id=order.id,
        quantity=Decimal("50.00000000"),
        fill_price=Decimal("648.75000000"),
        commission=Decimal("2.50000000"),
        slippage=Decimal("0.25000000"),
        currency_code="EUR",
        executed_at=datetime.now(UTC),
    )
    db_session.add(trade)
    await db_session.flush()
    assert trade.id is not None


@pytest.mark.asyncio
async def test_order_quantity_must_be_positive(db_session: AsyncSession):
    user = User(email="order_chk@quant.example", password_hash="hashed")
    exchange = Exchange(
        mic="XHKG",
        name="Hong Kong Exchanges",
        country_code="HK",
        timezone="Asia/Hong_Kong",
        currency_code="HKD",
        calendar_code="XHKG",
    )
    db_session.add_all([user, exchange])
    await db_session.flush()

    inst = Instrument(
        exchange_id=exchange.id,
        symbol="0700",
        name="Tencent Holdings",
        currency_code="HKD",
    )
    portfolio = Portfolio(
        user_id=user.id,
        name="HK Portfolio",
        base_currency="HKD",
        initial_capital=Decimal("5000000.00000000"),
        cash_balance=Decimal("5000000.00000000"),
    )
    db_session.add_all([inst, portfolio])
    await db_session.flush()

    # Zero or negative order quantity violates ck_order_positive_quantity
    bad_order = Order(
        portfolio_id=portfolio.id,
        instrument_id=inst.id,
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("0.00000000"),
        status="PENDING",
    )
    db_session.add(bad_order)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_trade_fill_price_must_be_positive(db_session: AsyncSession):
    user = User(email="trade_chk@quant.example", password_hash="hashed")
    exchange = Exchange(
        mic="XTKS",
        name="Tokyo Stock Exchange",
        country_code="JP",
        timezone="Asia/Tokyo",
        currency_code="JPY",
        calendar_code="XTKS",
    )
    db_session.add_all([user, exchange])
    await db_session.flush()

    inst = Instrument(
        exchange_id=exchange.id,
        symbol="7203",
        name="Toyota Motor",
        currency_code="JPY",
    )
    portfolio = Portfolio(
        user_id=user.id,
        name="Japan Alpha",
        base_currency="JPY",
        initial_capital=Decimal("50000000.00000000"),
        cash_balance=Decimal("50000000.00000000"),
    )
    db_session.add_all([inst, portfolio])
    await db_session.flush()

    order = Order(
        portfolio_id=portfolio.id,
        instrument_id=inst.id,
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("100.00000000"),
        status="PENDING",
    )
    db_session.add(order)
    await db_session.flush()

    bad_trade = Trade(
        order_id=order.id,
        quantity=Decimal("100.00000000"),
        fill_price=Decimal("0.00000000"),  # violates ck_trade_positive_fill_price
        currency_code="JPY",
        executed_at=datetime.now(UTC),
    )
    db_session.add(bad_trade)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_foreign_key_violation_rejection(db_session: AsyncSession):
    non_existent_id = uuid.uuid4()
    bad_instrument = Instrument(
        exchange_id=non_existent_id,
        symbol="GHOST",
        name="Ghost Asset",
        currency_code="USD",
    )
    db_session.add(bad_instrument)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_experiment_and_simulation_job_relationships(db_session: AsyncSession):
    user = User(email="researcher@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    strategy = Strategy(
        user_id=user.id,
        name="Bollinger Band Mean Reversion",
        strategy_type="MEAN_REVERSION",
    )
    db_session.add(strategy)
    await db_session.flush()

    version = StrategyVersion(
        strategy_id=strategy.id,
        version=1,
        parameters={"window": 20, "std_dev": 2.0},
    )
    db_session.add(version)
    await db_session.flush()

    experiment = Experiment(
        user_id=user.id,
        strategy_version_id=version.id,
        experiment_type="BACKTEST",
        status="COMPLETED",
        configuration={"start_date": "2023-01-01", "end_date": "2023-12-31"},
        engine_version="0.1.0",
        random_seed=42,
    )
    db_session.add(experiment)
    await db_session.flush()

    job = SimulationJob(
        user_id=user.id,
        experiment_id=experiment.id,
        job_type="HISTORICAL_SIMULATION",
        status="RUNNING",
        progress=75,
        result_reference="s3://quant-results/exp_001.parquet",
    )
    db_session.add(job)
    await db_session.flush()

    assert experiment.id is not None
    assert job.id is not None
    assert job.experiment_id == experiment.id
    assert job.progress == 75


@pytest.mark.asyncio
async def test_risk_report_relationships(db_session: AsyncSession):
    user = User(email="risk_officer@quant.example", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()

    portfolio = Portfolio(
        user_id=user.id,
        name="Global Macro",
        base_currency="USD",
        initial_capital=Decimal("25000000.00000000"),
        cash_balance=Decimal("25000000.00000000"),
    )
    db_session.add(portfolio)
    await db_session.flush()

    report = RiskReport(
        user_id=user.id,
        portfolio_id=portfolio.id,
        method="HISTORICAL_VAR",
        configuration={"confidence_interval": 0.99, "time_horizon_days": 10},
        results={"var_usd": 450000.0, "cvar_usd": 620000.0},
    )
    db_session.add(report)
    await db_session.flush()

    assert report.id is not None
    assert report.portfolio_id == portfolio.id
    assert report.method == "HISTORICAL_VAR"
    assert report.results["var_usd"] == 450000.0
