import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email import EmailSender, build_email_sender
from app.core.security import decode_access_token
from app.db.session import get_db
from app.market_data.base import MarketDataProvider
from app.market_data.service import MarketDataService
from app.market_data.synthetic import SyntheticProvider
from app.market_data.yahoo import YahooFinanceProvider
from app.models.identity import User
from app.trading.quotes import QuoteService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Authenticate the bearer token and return the active User record.
    Raises HTTPException(401) on missing, expired, or invalid token.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from err
    except jwt.InvalidTokenError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from err

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from err

    stmt = select(User).where(User.id == user_uuid)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )

    # Tokens issued before the user's sessions were ended (password change, reset,
    # sign-out everywhere) are refused even though their signature is still valid.
    if (
        user.tokens_valid_after is not None
        and float(payload["iat"]) < user.tokens_valid_after.timestamp()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def require_authenticated_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Alias dependency explicitly stating the authentication requirement."""
    return current_user


_default_provider: MarketDataProvider = (
    SyntheticProvider() if settings.market_data_provider == "synthetic" else YahooFinanceProvider()
)
_default_email_sender = build_email_sender()


def get_email_sender() -> EmailSender:
    """Outgoing email. Override in tests to capture messages."""
    return _default_email_sender


def get_market_data_provider() -> MarketDataProvider:
    """Upstream market data provider. Override in tests to avoid network access."""
    return _default_provider


def get_market_data_service(
    db: AsyncSession = Depends(get_db),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> MarketDataService:
    return MarketDataService(db, provider=provider)


def get_quote_service(
    db: AsyncSession = Depends(get_db),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> QuoteService:
    return QuoteService(db, provider=provider)
