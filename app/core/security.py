import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings

# Initialize password hasher using Argon2id
password_hash_helper = PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return password_hash_helper.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    return password_hash_helper.verify(plain_password, hashed_password)


def create_access_token(subject: str | uuid.UUID, expires_delta: timedelta | None = None) -> str:
    """
    Generate a signed JWT access token.
    'sub' is the string representation of the user's UUID.
    """
    now = datetime.now(UTC)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.access_token_minutes)

    payload = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "type": "access",
    }

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict:
    """
    Decode and verify a signed JWT token.
    Raises jwt.PyJWTError (ExpiredSignatureError, InvalidTokenError, etc.) on failure.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "exp", "iat"]},
    )
