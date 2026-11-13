import uuid
from collections.abc import AsyncGenerator, Callable

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_email_sender, get_market_data_provider
from app.core.config import settings
from app.core.email import Email
from app.core.ratelimit import limiter, run_slots
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import app as fastapi_app
from app.market_data.seed import seed_market_reference_data
from app.models.identity import Exchange, Instrument, User
from app.trading.quotes import QuoteService
from helpers import FakeProvider


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def test_schema_name():
    return f"test_quant_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
async def test_engine(test_schema_name):
    # Base admin connection to create/drop the isolated schema
    admin_engine = create_async_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {test_schema_name}"))

    # Engine configured with isolated search_path
    schema_engine = create_async_engine(
        settings.database_url,
        echo=False,
        connect_args={"server_settings": {"search_path": test_schema_name}},
    )

    # Create tables in the isolated test schema
    async with schema_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield schema_engine

    # Teardown: drop tables and isolated schema
    async with schema_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await schema_engine.dispose()

    async with admin_engine.connect() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {test_schema_name} CASCADE"))
    await admin_engine.dispose()


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Provides a transactional session that rolls back after each test
    to guarantee strict isolation and clean state between tests.
    """
    connection = await test_engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    session = session_factory()

    yield session

    await session.close()
    if transaction.is_active:
        await transaction.rollback()
    await connection.close()


@pytest.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Test client for FastAPI overriding get_db dependency with test database session.
    """
    async def override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Shared helpers for the strategy / backtest / trading / risk suites
# ---------------------------------------------------------------------------



@pytest.fixture(autouse=True)
def _reset_limits():
    """Rate-limit counters are process-wide; every test starts with none used."""
    limiter.reset()
    run_slots.reset()
    yield
    limiter.reset()
    run_slots.reset()


@pytest.fixture
def sent_emails(async_client: AsyncClient) -> list[Email]:
    """Capture outgoing email instead of logging it."""
    outbox: list[Email] = []

    class _Outbox:
        async def send(self, email: Email) -> None:
            outbox.append(email)

    fastapi_app.dependency_overrides[get_email_sender] = lambda: _Outbox()
    return outbox


@pytest.fixture(autouse=True)
def _clear_quote_cache():
    QuoteService.clear_cache()
    yield
    QuoteService.clear_cache()


@pytest.fixture
def fake_provider(async_client: AsyncClient) -> FakeProvider:
    """Replace the upstream provider for the duration of a test (no network)."""
    provider = FakeProvider()
    fastapi_app.dependency_overrides[get_market_data_provider] = lambda: provider
    return provider


@pytest.fixture
async def make_user(db_session: AsyncSession) -> Callable:
    """Factory returning ``(user, auth_headers)``. Hashing is skipped for speed."""

    async def _make(email: str | None = None) -> tuple[User, dict[str, str]]:
        user = User(
            email=email or f"user_{uuid.uuid4().hex[:10]}@quant.example",
            password_hash="not-a-real-hash",
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()
        token = create_access_token(subject=user.id)
        return user, {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
async def auth_headers(make_user) -> dict[str, str]:
    _, headers = await make_user()
    return headers


@pytest.fixture
async def instruments(db_session: AsyncSession) -> dict[str, Instrument]:
    """Seeded reference instruments keyed by ``MIC:SYMBOL`` (e.g. ``XNAS:AAPL``)."""
    await seed_market_reference_data(db_session)
    await db_session.flush()
    rows = await db_session.execute(
        select(Instrument, Exchange.mic).join(Exchange, Exchange.id == Instrument.exchange_id)
    )
    return {f"{mic}:{instrument.symbol}": instrument for instrument, mic in rows.all()}
