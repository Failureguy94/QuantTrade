import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool, QueuePool

from app.db.session import engine, get_db


@pytest.mark.asyncio
async def test_sqlalchemy_async_engine_connectivity():
    """Verify application connects using its SQLAlchemy async engine."""
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_intended_postgresql_database():
    """Verify the connection uses the intended PostgreSQL database ('quant')."""
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT current_database(), current_user;"))
        row = result.fetchone()
        assert row is not None
        db_name, db_user = row
        assert db_name == "quant"
        assert db_user == "quant"


@pytest.mark.asyncio
async def test_connection_pool_configuration():
    """Verify connection pool configuration on the engine."""
    assert engine.pool is not None
    # engine.pool.pre_ping is True as configured in session.py
    assert engine.pool._pre_ping is True
    # Verify pool size configuration or type
    assert hasattr(engine.pool, "size") or isinstance(engine.pool, (QueuePool, NullPool))


@pytest.mark.asyncio
async def test_session_creation_and_cleanup():
    """Verify session creation, transaction handling and cleanup using get_db."""
    generator = get_db()
    session = await anext(generator)
    assert isinstance(session, AsyncSession)
    assert session.is_active

    # Execute a simple query
    res = await session.execute(text("SELECT 42"))
    assert res.scalar() == 42

    # Clean up
    try:
        await anext(generator)
    except StopAsyncIteration:
        pass


@pytest.mark.asyncio
async def test_isolated_session_fixture(db_session: AsyncSession):
    """Verify that test db_session works against the isolated schema."""
    res = await db_session.execute(text("SELECT 100"))
    assert res.scalar() == 100
