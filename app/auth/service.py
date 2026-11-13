"""Accounts and sessions: registration, sign-in, token rotation, email links, deletion."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError, UnprocessableError
from app.core.ratelimit import RateLimitExceeded, limiter
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    verify_password,
)
from app.models.identity import EmailToken, RefreshToken, User
from app.models.portfolio import (
    LedgerEntry,
    LedgerTransaction,
    Order,
    OrderEvent,
    Portfolio,
    Trade,
)
from app.models.research import Experiment, RiskReport, SimulationJob, Strategy

logger = logging.getLogger(__name__)

PURPOSE_VERIFY_EMAIL = "VERIFY_EMAIL"
PURPOSE_RESET_PASSWORD = "RESET_PASSWORD"

_INVALID_CREDENTIALS = "Incorrect email or password"
_INVALID_REFRESH = "Refresh token is invalid or expired"
_INVALID_LINK = "This link is invalid or has expired"

# Verified against when the email is unknown, so that case takes as long as a wrong
# password and cannot be told apart by timing.
_dummy_hash: str | None = None


def _burn_password_check(password: str) -> None:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(new_opaque_token())
    verify_password(password, _dummy_hash)


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------ accounts
    async def get_user_by_email(self, email: str) -> User | None:
        return (
            await self.db.execute(select(User).where(User.email == email.strip().lower()))
        ).scalar_one_or_none()

    async def register(self, email: str, password: str) -> tuple[User, str]:
        """Create a user. Returns it with the token for its verification email."""
        email = email.strip().lower()
        if await self.get_user_by_email(email) is not None:
            raise ConflictError("A user with this email already exists", code="email_taken")
        user = User(email=email, password_hash=hash_password(password), is_active=True)
        self.db.add(user)
        await self.db.flush()
        token = await self.create_email_token(user, PURPOSE_VERIFY_EMAIL)
        return user, token

    async def authenticate(self, email: str, password: str) -> User:
        """Check credentials. Failures are generic and repeated ones are locked out.

        The lockout counts failures per email whether or not an account exists, so
        it does not reveal which emails are registered.
        """
        email = email.strip().lower()
        lock_key = f"login-failures:{email}"
        window = settings.login_lockout_minutes * 60.0
        if settings.rate_limit_enabled:
            wait = limiter.retry_after(lock_key, settings.login_max_failures, window)
            if wait:
                raise RateLimitExceeded(
                    wait, detail="Too many failed sign-in attempts. Try again later."
                )

        user = await self.get_user_by_email(email)
        if user is None:
            _burn_password_check(password)
            valid = False
        else:
            valid = verify_password(password, user.password_hash)
        if user is None or not valid:
            if settings.rate_limit_enabled:
                limiter.record(lock_key)
            raise UnauthorizedError(_INVALID_CREDENTIALS, code="invalid_credentials")

        limiter.clear(lock_key)
        if not user.is_active:
            raise ForbiddenError("Inactive user account", code="inactive_user")
        if settings.require_email_verification and user.email_verified_at is None:
            raise ForbiddenError(
                "Confirm your email address before signing in", code="email_not_verified"
            )
        return user

    async def change_password(self, user: User, current: str, new: str) -> TokenPair:
        if not verify_password(current, user.password_hash):
            raise UnprocessableError("Current password is incorrect", code="wrong_password")
        await self._set_password(user, new)
        return await self.issue_tokens(user)

    async def _set_password(self, user: User, new_password: str) -> None:
        """Replace the password and end every existing session."""
        user.password_hash = hash_password(new_password)
        await self.revoke_all_sessions(user)

    async def delete_account(self, user: User, password: str) -> None:
        """Erase the user and everything they own.

        Paper trades, their ledger and experiments are protected from accidental
        cascades by RESTRICT foreign keys, so the rows are removed explicitly, leaves
        first (ADR-015).
        """
        if not verify_password(password, user.password_hash):
            raise UnprocessableError("Password is incorrect", code="wrong_password")

        portfolios = select(Portfolio.id).where(Portfolio.user_id == user.id)
        orders = select(Order.id).where(Order.portfolio_id.in_(portfolios))
        await self.db.execute(delete(LedgerEntry).where(LedgerEntry.portfolio_id.in_(portfolios)))
        await self.db.execute(
            delete(LedgerTransaction).where(LedgerTransaction.portfolio_id.in_(portfolios))
        )
        await self.db.execute(delete(Trade).where(Trade.order_id.in_(orders)))
        await self.db.execute(delete(OrderEvent).where(OrderEvent.order_id.in_(orders)))
        await self.db.execute(delete(Order).where(Order.portfolio_id.in_(portfolios)))
        # Positions, snapshots and allocations go with the portfolio.
        await self.db.execute(delete(Portfolio).where(Portfolio.user_id == user.id))
        await self.db.execute(delete(RiskReport).where(RiskReport.user_id == user.id))
        await self.db.execute(delete(SimulationJob).where(SimulationJob.user_id == user.id))
        await self.db.execute(delete(Experiment).where(Experiment.user_id == user.id))
        # Versions go with the strategy.
        await self.db.execute(delete(Strategy).where(Strategy.user_id == user.id))
        # Sessions and email tokens go with the user.
        await self.db.execute(delete(User).where(User.id == user.id))
        await self.db.flush()
        logger.info("Deleted account %s", user.id)

    # ------------------------------------------------------------------ sessions
    async def issue_tokens(self, user: User, family_id: uuid.UUID | None = None) -> TokenPair:
        refresh_token = new_opaque_token()
        self.db.add(
            RefreshToken(
                user_id=user.id,
                family_id=family_id or uuid.uuid4(),
                token_hash=hash_token(refresh_token),
                expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
            )
        )
        await self.db.flush()
        return TokenPair(
            access_token=create_access_token(subject=user.id),
            refresh_token=refresh_token,
            expires_in=settings.access_token_minutes * 60,
        )

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Exchange a refresh token for a new pair, retiring the one presented."""
        row = (
            await self.db.execute(
                select(RefreshToken)
                .where(RefreshToken.token_hash == hash_token(refresh_token))
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            raise UnauthorizedError(_INVALID_REFRESH, code="invalid_refresh_token")

        if row.rotated_at is not None:
            grace = timedelta(seconds=settings.refresh_token_grace_seconds)
            if now - row.rotated_at > grace:
                # A retired token came back: it was copied. End the whole session
                # family, and keep that even though this request fails.
                await self._revoke_family(row.family_id, now)
                await self.db.commit()
                logger.warning("Refresh token reuse detected for user %s", row.user_id)
                raise UnauthorizedError(_INVALID_REFRESH, code="invalid_refresh_token")
            # Inside the grace period: a concurrent request of the same client.
        else:
            row.rotated_at = now

        user = await self.db.get(User, row.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError(_INVALID_REFRESH, code="invalid_refresh_token")
        return await self.issue_tokens(user, family_id=row.family_id)

    async def logout(self, refresh_token: str) -> None:
        """End the session the token belongs to. Unknown tokens are ignored."""
        family_id = (
            await self.db.execute(
                select(RefreshToken.family_id).where(
                    RefreshToken.token_hash == hash_token(refresh_token)
                )
            )
        ).scalar_one_or_none()
        if family_id is not None:
            await self._revoke_family(family_id, datetime.now(UTC))

    async def revoke_all_sessions(self, user: User) -> None:
        """Revoke every refresh token and refuse every access token issued so far."""
        now = datetime.now(UTC)
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        user.tokens_valid_after = now
        await self.db.flush()

    async def _revoke_family(self, family_id: uuid.UUID, now: datetime) -> None:
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    # --------------------------------------------------------------- email links
    async def create_email_token(self, user: User, purpose: str) -> str:
        """Issue a single-use token, replacing any unused one of the same purpose."""
        await self.db.execute(
            delete(EmailToken).where(
                EmailToken.user_id == user.id,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
            )
        )
        lifetime = (
            timedelta(hours=settings.email_verification_hours)
            if purpose == PURPOSE_VERIFY_EMAIL
            else timedelta(minutes=settings.password_reset_minutes)
        )
        token = new_opaque_token()
        self.db.add(
            EmailToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=hash_token(token),
                expires_at=datetime.now(UTC) + lifetime,
            )
        )
        await self.db.flush()
        return token

    async def _consume_email_token(self, token: str, purpose: str) -> User:
        row = (
            await self.db.execute(
                select(EmailToken)
                .where(EmailToken.token_hash == hash_token(token), EmailToken.purpose == purpose)
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None or row.used_at is not None or row.expires_at <= now:
            raise UnprocessableError(_INVALID_LINK, code="invalid_token")
        user = await self.db.get(User, row.user_id)
        if user is None or not user.is_active:
            raise UnprocessableError(_INVALID_LINK, code="invalid_token")
        row.used_at = now
        return user

    async def verify_email(self, token: str) -> User:
        user = await self._consume_email_token(token, PURPOSE_VERIFY_EMAIL)
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
        await self.db.flush()
        return user

    async def reset_password(self, token: str, new_password: str) -> User:
        user = await self._consume_email_token(token, PURPOSE_RESET_PASSWORD)
        await self._set_password(user, new_password)
        # Receiving the link proves control of the mailbox.
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
        limiter.clear(f"login-failures:{user.email}")
        await self.db.flush()
        return user
