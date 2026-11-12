from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.identity import User
from app.schemas.auth import Token, UserCreate, UserLogin, UserRead

router = APIRouter()


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Register a new user with normalized email and Argon2id hashed password.
    Returns safe user information without exposing password hashes.
    """
    normalized_email = user_in.email.strip().lower()

    # Check if user already exists
    stmt = select(User).where(User.email == normalized_email)
    result = await db.execute(stmt)
    existing_user = result.scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    # Hash password with Argon2id
    hashed_pwd = hash_password(user_in.password)

    user = User(
        email=normalized_email,
        password_hash=hashed_pwd,
        is_active=True,
    )
    db_session = db
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@router.post(
    "/login",
    response_model=Token,
    summary="Authenticate and receive access token",
)
async def login(
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """
    Verify user credentials and return a signed JWT access token.
    Uses generic error responses to prevent user enumeration.
    """
    normalized_email = credentials.email.strip().lower()

    stmt = select(User).where(User.email == normalized_email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )

    access_token = create_access_token(subject=user.id)
    return Token(access_token=access_token, token_type="bearer")


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
