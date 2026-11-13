import hashlib
import secrets
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
        # Sub-second precision: a token is compared with the moment a user's
        # sessions were invalidated, which can fall in the same second.
        "iat": now.timestamp(),
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


def new_opaque_token() -> str:
    """A random URL-safe secret for refresh tokens and emailed links."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """SHA-256 of an opaque token. Tokens are high-entropy, so no salt or stretching
    is needed; storing only the hash means a database leak does not leak sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
