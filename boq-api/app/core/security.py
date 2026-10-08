import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher()

# Used to equalise timing when the email does not exist, so login can't be used to enumerate users.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str) -> str | None:
    """Returns an error message or None."""
    if len(password) < 10:
        return "Password must be at least 10 characters"
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        return "Password must mix at least 3 of: lowercase, uppercase, digits, symbols"
    return None


def create_access_token(user_id: uuid.UUID, token_version: int, session_id: uuid.UUID) -> tuple[str, int]:
    now = datetime.now(UTC)
    ttl = settings.ACCESS_TOKEN_MINUTES * 60
    payload = {
        "sub": str(user_id),
        "tv": token_version,
        "sid": str(session_id),
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "typ": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM), ttl


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
