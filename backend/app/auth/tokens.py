from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User

JWT_ALGORITHM = "HS256"
JWT_TOKEN_TYPE = "access"
JWT_SECRET_KEY_ENV = "JWT_SECRET_KEY"
JWT_EXPIRE_ENV = "JWT_ACCESS_TOKEN_EXPIRE_MINUTES"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 15
MIN_ACCESS_TOKEN_EXPIRE_MINUTES = 1
MAX_ACCESS_TOKEN_EXPIRE_MINUTES = 60
MIN_SECRET_LENGTH = 32


class AuthConfigurationError(RuntimeError):
    """Raised when required authentication configuration is unavailable."""


class InvalidTokenError(ValueError):
    """Raised for any invalid, expired, or unusable access token."""


def get_jwt_secret_key() -> str:
    secret = os.getenv(JWT_SECRET_KEY_ENV, "").strip()
    if len(secret) < MIN_SECRET_LENGTH:
        raise AuthConfigurationError("JWT authentication is not configured.")
    return secret


def get_access_token_expire_minutes() -> int:
    raw = os.getenv(JWT_EXPIRE_ENV, str(DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES)).strip()
    try:
        minutes = int(raw)
    except ValueError as exc:
        raise AuthConfigurationError("JWT authentication is not configured.") from exc
    if not MIN_ACCESS_TOKEN_EXPIRE_MINUTES <= minutes <= MAX_ACCESS_TOKEN_EXPIRE_MINUTES:
        raise AuthConfigurationError("JWT authentication is not configured.")
    return minutes


def create_access_token(user_id: uuid.UUID | str) -> str:
    try:
        parsed_user_id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("User id must be a valid UUID.") from exc
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=get_access_token_expire_minutes())
    payload = {
        "sub": str(parsed_user_id),
        "exp": expires,
        "type": JWT_TOKEN_TYPE,
    }
    return jwt.encode(payload, get_jwt_secret_key(), algorithm=JWT_ALGORITHM)


async def authenticate_token(token: str, session: AsyncSession) -> User:
    """Decode an access token and return its active database user.

    This function is deliberately transport-agnostic so HTTP and WebSocket
    authentication can share the same token and user checks.
    """
    if not isinstance(token, str) or not token.strip() or len(token) > 4096:
        raise InvalidTokenError("Invalid access token.")
    try:
        claims = jwt.decode(
            token,
            get_jwt_secret_key(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "type"]},
        )
    except (jwt.PyJWTError, AuthConfigurationError) as exc:
        raise InvalidTokenError("Invalid access token.") from exc

    if claims.get("type") != JWT_TOKEN_TYPE or not isinstance(claims.get("sub"), str):
        raise InvalidTokenError("Invalid access token.")
    try:
        user_id = uuid.UUID(claims["sub"])
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidTokenError("Invalid access token.") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError("Invalid access token.")
    return user

