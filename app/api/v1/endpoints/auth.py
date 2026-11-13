"""Accounts and sessions."""

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_email_sender
from app.api.limits import auth_rate_limit
from app.auth.service import PURPOSE_RESET_PASSWORD, PURPOSE_VERIFY_EMAIL, AuthService, TokenPair
from app.core.email import EmailSender, password_reset_email, send_safely, verification_email
from app.db.session import get_db
from app.models.identity import User
from app.schemas.auth import (
    AccountDelete,
    EmailRequest,
    EmailTokenRequest,
    Message,
    PasswordChange,
    PasswordResetConfirm,
    RefreshRequest,
    Token,
    UserCreate,
    UserLogin,
    UserRead,
)

router = APIRouter()


def _token(pair: TokenPair) -> Token:
    return Token(
        access_token=pair.access_token,
        token_type="bearer",
        expires_in=pair.expires_in,
        refresh_token=pair.refresh_token,
    )


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    dependencies=[Depends(auth_rate_limit)],
)
async def register(
    user_in: UserCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> User:
    """
    Register a new user with normalized email and Argon2id hashed password, and send
    the email-verification link. Returns safe user information only.
    """
    user, token = await AuthService(db).register(user_in.email, user_in.password)
    await db.commit()
    background.add_task(send_safely, email_sender, verification_email(user.email, token))
    return user


@router.post(
    "/login",
    response_model=Token,
    summary="Authenticate and receive tokens",
    dependencies=[Depends(auth_rate_limit)],
)
async def login(
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """
    Verify credentials and return a short-lived access token with a refresh token.
    Errors are generic to prevent user enumeration; repeated failures for one email
    are locked out for a while.
    """
    service = AuthService(db)
    user = await service.authenticate(credentials.email, credentials.password)
    pair = await service.issue_tokens(user)
    await db.commit()
    return _token(pair)


@router.post(
    "/refresh",
    response_model=Token,
    summary="Exchange a refresh token for a new token pair",
    dependencies=[Depends(auth_rate_limit)],
)
async def refresh_tokens(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> Token:
    """
    Refresh tokens are single-use: each call returns a new one and retires the one
    presented. Presenting a retired token again ends the whole session.
    """
    pair = await AuthService(db).refresh(payload.refresh_token)
    await db.commit()
    return _token(pair)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the session a refresh token belongs to",
)
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> Response:
    await AuthService(db).logout(payload.refresh_token)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End every session of the current user",
)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revokes all refresh tokens and every access token issued so far."""
    await AuthService(db).revoke_all_sessions(current_user)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get current user profile",
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Return the profile of the currently authenticated user.
    """
    return current_user


@router.post(
    "/verify-email",
    response_model=UserRead,
    summary="Confirm an email address with the emailed token",
    dependencies=[Depends(auth_rate_limit)],
)
async def verify_email(payload: EmailTokenRequest, db: AsyncSession = Depends(get_db)) -> User:
    user = await AuthService(db).verify_email(payload.token)
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/resend-verification",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send the email-verification link again",
    dependencies=[Depends(auth_rate_limit)],
)
async def resend_verification(
    payload: EmailRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> Message:
    """Always answers the same way, whether or not the email has an account."""
    service = AuthService(db)
    user = await service.get_user_by_email(payload.email)
    if user is not None and user.is_active and user.email_verified_at is None:
        token = await service.create_email_token(user, PURPOSE_VERIFY_EMAIL)
        await db.commit()
        background.add_task(send_safely, email_sender, verification_email(user.email, token))
    return Message(detail="If the address needs confirming, a new link has been sent.")


@router.post(
    "/forgot-password",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Email a password-reset link",
    dependencies=[Depends(auth_rate_limit)],
)
async def forgot_password(
    payload: EmailRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> Message:
    """Always answers the same way, whether or not the email has an account."""
    service = AuthService(db)
    user = await service.get_user_by_email(payload.email)
    if user is not None and user.is_active:
        token = await service.create_email_token(user, PURPOSE_RESET_PASSWORD)
        await db.commit()
        background.add_task(send_safely, email_sender, password_reset_email(user.email, token))
    return Message(detail="If the address has an account, a reset link has been sent.")


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password with the emailed token",
    dependencies=[Depends(auth_rate_limit)],
)
async def reset_password(
    payload: PasswordResetConfirm, db: AsyncSession = Depends(get_db)
) -> Response:
    """Ends every existing session of the account."""
    await AuthService(db).reset_password(payload.token, payload.new_password)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/change-password",
    response_model=Token,
    summary="Change the current user's password",
)
async def change_password(
    payload: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Ends every other session and returns a fresh token pair for this one."""
    pair = await AuthService(db).change_password(
        current_user, payload.current_password, payload.new_password
    )
    await db.commit()
    return _token(pair)


@router.post(
    "/delete-account",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete the current user and all their data",
)
async def delete_account(
    payload: AccountDelete,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Removes strategies, experiments, portfolios, orders, ledger and reports."""
    await AuthService(db).delete_account(current_user, payload.password)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
