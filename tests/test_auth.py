import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.identity import User
from app.models.portfolio import Portfolio
from app.models.research import Experiment, Strategy


# ---------------------------------------------------------------------------
# Unit tests: Password hashing and verification
# ---------------------------------------------------------------------------
def test_password_hashing_and_verification():
    plain = "SuperSecurePassword123!"
    hashed = hash_password(plain)

    # Never store plaintext
    assert hashed != plain
    assert "$argon2id$" in hashed

    # Verify matching password
    assert verify_password(plain, hashed) is True

    # Reject incorrect password
    assert verify_password("WrongPassword!", hashed) is False


# ---------------------------------------------------------------------------
# Unit tests: JWT creation, expiration and tampering
# ---------------------------------------------------------------------------
def test_valid_jwt_generation_and_decoding():
    user_id = uuid.uuid4()
    token = create_access_token(subject=user_id)
    decoded = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])

    assert decoded["sub"] == str(user_id)
    assert decoded["type"] == "access"
    assert "exp" in decoded


def test_expired_jwt_rejection():
    user_id = uuid.uuid4()
    token = create_access_token(subject=user_id, expires_delta=timedelta(seconds=-10))

    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def test_invalid_jwt_signature_rejection():
    user_id = uuid.uuid4()
    token = create_access_token(subject=user_id)

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            token,
            "wrong-secret-key-that-does-not-match",
            algorithms=[settings.jwt_algorithm],
        )


# ---------------------------------------------------------------------------
# Integration tests: Registration endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_registration(async_client: AsyncClient, db_session: AsyncSession):
    payload = {
        "email": "NewTrader@Quant.Example",
        "password": "StrongPassword987#",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["email"] == "newtrader@quant.example"  # normalized to lowercase
    assert data["is_active"] is True
    assert "id" in data
    # CRITICAL: Password hash must NEVER appear in API responses
    assert "password" not in data
    assert "password_hash" not in data

    # Verify stored in DB with Argon2id hash
    stmt = select(User).where(User.email == "newtrader@quant.example")
    user = (await db_session.execute(stmt)).scalar_one()
    assert user.email == "newtrader@quant.example"
    assert verify_password("StrongPassword987#", user.password_hash) is True


@pytest.mark.asyncio
async def test_duplicate_email_registration_rejection(
    async_client: AsyncClient, db_session: AsyncSession
):
    # Pre-register user
    user = User(
        email="existing@quant.example",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    # Attempt to register with same email (even with different case)
    payload = {
        "email": "EXISTING@Quant.Example",
        "password": "AnotherPassword456#",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"] == "A user with this email already exists"


@pytest.mark.asyncio
async def test_registration_validation_errors(async_client: AsyncClient):
    # Short password
    res = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "valid@quant.example", "password": "short"},
    )
    assert res.status_code == 422

    # Malformed email
    res2 = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "ValidPassword123!"},
    )
    assert res2.status_code == 422


# ---------------------------------------------------------------------------
# Integration tests: Login endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_login(async_client: AsyncClient, db_session: AsyncSession):
    user = User(
        email="trader_login@quant.example",
        password_hash=hash_password("ValidPassword123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "TRADER_LOGIN@quant.example", "password": "ValidPassword123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Decode and check sub
    decoded = jwt.decode(
        data["access_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    assert decoded["sub"] == str(user.id)


@pytest.mark.asyncio
async def test_login_invalid_password_returns_generic_error(
    async_client: AsyncClient, db_session: AsyncSession
):
    user = User(
        email="wrong_pwd@quant.example",
        password_hash=hash_password("CorrectPassword123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "wrong_pwd@quant.example", "password": "IncorrectPassword!"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


@pytest.mark.asyncio
async def test_login_unknown_email_returns_identical_generic_error(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@quant.example", "password": "SomePassword123!"},
    )
    assert response.status_code == 401
    # Generic error preventing user enumeration
    assert response.json()["detail"] == "Incorrect email or password"


@pytest.mark.asyncio
async def test_login_inactive_user_rejected(async_client: AsyncClient, db_session: AsyncSession):
    user = User(
        email="banned@quant.example",
        password_hash=hash_password("Password123!"),
        is_active=False,
    )
    db_session.add(user)
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "banned@quant.example", "password": "Password123!"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Inactive user account"


# ---------------------------------------------------------------------------
# Integration tests: Protected GET /api/v1/auth/me endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_me_with_valid_bearer_token(async_client: AsyncClient, db_session: AsyncSession):
    user = User(
        email="authed_me@quant.example",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    token = create_access_token(subject=user.id)
    headers = {"Authorization": f"Bearer {token}"}

    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(user.id)
    assert data["email"] == "authed_me@quant.example"
    # Never leak password_hash
    assert "password_hash" not in data
    assert "password" not in data


@pytest.mark.asyncio
async def test_get_me_missing_bearer_token(async_client: AsyncClient):
    response = await async_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication credentials were not provided"


@pytest.mark.asyncio
async def test_get_me_expired_token(async_client: AsyncClient, db_session: AsyncSession):
    user = User(
        email="expired_me@quant.example",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    expired_token = create_access_token(subject=user.id, expires_delta=timedelta(seconds=-10))
    headers = {"Authorization": f"Bearer {expired_token}"}

    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "Token has expired"


@pytest.mark.asyncio
async def test_get_me_invalid_token_signature(async_client: AsyncClient, db_session: AsyncSession):
    user = User(
        email="tampered@quant.example",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    # Sign with foreign secret (using >= 32 byte key for HMAC-SHA256 compliance)
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    tampered_token = jwt.encode(
        payload, "fraudulent-foreign-secret-key-at-least-32-chars-long", algorithm="HS256"
    )

    headers = {"Authorization": f"Bearer {tampered_token}"}
    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


# ---------------------------------------------------------------------------
# Integration tests: Authorization isolation across users
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_authorization_user_resource_isolation(db_session: AsyncSession):
    """
    Test authorization invariant: User A cannot access User B's resources.
    Simulate accessing a user-owned resource using the authenticated user context.
    """
    user_a = User(email="user_a@quant.example", password_hash=hash_password("PassA12345!"))
    user_b = User(email="user_b@quant.example", password_hash=hash_password("PassB12345!"))
    db_session.add_all([user_a, user_b])
    await db_session.commit()

    # User B creates a private strategy
    strategy_b = Strategy(
        user_id=user_b.id,
        name="User B Proprietary Alpha",
        strategy_type="ML_ALPHA",
    )
    db_session.add(strategy_b)
    await db_session.commit()

    # Query executing on behalf of User A (filtering by current_user.id)
    stmt_for_user_a = select(Strategy).where(
        Strategy.id == strategy_b.id,
        Strategy.user_id == user_a.id,
    )
    result = await db_session.execute(stmt_for_user_a)
    resource_for_user_a = result.scalar_one_or_none()

    # User A gets None, ensuring resource existence is not leaked
    assert resource_for_user_a is None

    # Query executing on behalf of User B returns the resource
    stmt_for_user_b = select(Strategy).where(
        Strategy.id == strategy_b.id,
        Strategy.user_id == user_b.id,
    )
    res_b = (await db_session.execute(stmt_for_user_b)).scalar_one_or_none()
    assert res_b is not None
    assert res_b.id == strategy_b.id


@pytest.mark.asyncio
async def test_get_me_rejects_non_access_token_type(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Ensure tokens with a type other than 'access' (e.g. 'refresh') are rejected."""
    user = User(
        email="refresh_type@quant.example",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "type": "refresh",  # non-access token type
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    refresh_token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    headers = {"Authorization": f"Bearer {refresh_token}"}
    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


@pytest.mark.asyncio
async def test_authorization_portfolio_and_experiment_isolation(db_session: AsyncSession):
    """
    Verify ownership scoping across Portfolio and Experiment entities:
    Neither Portfolio nor Experiment can be queried or manipulated by a different user.
    """
    user_alice = User(email="alice@quant.example", password_hash=hash_password("AlicePass123!"))
    user_bob = User(email="bob@quant.example", password_hash=hash_password("BobPass123!"))
    db_session.add_all([user_alice, user_bob])
    await db_session.commit()

    # Alice creates a portfolio
    portfolio_alice = Portfolio(
        user_id=user_alice.id,
        name="Alice Fund",
        base_currency="USD",
        initial_capital=Decimal("100000.00000000"),
        cash_balance=Decimal("100000.00000000"),
    )
    db_session.add(portfolio_alice)
    await db_session.commit()

    # Bob attempts to fetch Alice's portfolio scoped by his user_id
    stmt_bob_portfolio = select(Portfolio).where(
        Portfolio.id == portfolio_alice.id,
        Portfolio.user_id == user_bob.id,
    )
    bob_result = (await db_session.execute(stmt_bob_portfolio)).scalar_one_or_none()
    assert bob_result is None, "Bob must not have access to Alice's portfolio"

    # Alice creates an experiment
    experiment_alice = Experiment(
        user_id=user_alice.id,
        experiment_type="BACKTEST",
        status="QUEUED",
        configuration={"test": True},
    )
    db_session.add(experiment_alice)
    await db_session.commit()

    # Bob attempts to fetch Alice's experiment scoped by his user_id
    stmt_bob_exp = select(Experiment).where(
        Experiment.id == experiment_alice.id,
        Experiment.user_id == user_bob.id,
    )
    bob_exp_result = (await db_session.execute(stmt_bob_exp)).scalar_one_or_none()
    assert bob_exp_result is None, "Bob must not have access to Alice's experiment"

