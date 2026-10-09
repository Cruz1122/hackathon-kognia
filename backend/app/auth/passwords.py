from __future__ import annotations

import os

import bcrypt

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72


class PasswordValidationError(ValueError):
    """Raised when a password cannot be safely processed by bcrypt."""


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str):
        raise PasswordValidationError("Invalid password.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordValidationError("Password is too short.")
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PasswordValidationError("Invalid password.") from exc
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise PasswordValidationError("Password is too long.")
    return encoded


def hash_password(password: str) -> str:
    """Hash a bounded password; the plaintext never leaves this function."""
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password, returning False for malformed or invalid hashes."""
    try:
        encoded = _password_bytes(password)
        if not isinstance(password_hash, str) or not password_hash:
            return False
        return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
    except (UnicodeEncodeError, ValueError, TypeError, bcrypt.Error):
        return False


# Used only to keep missing-user login timing close to invalid-password timing.
DUMMY_PASSWORD_HASH = bcrypt.hashpw(os.urandom(32), bcrypt.gensalt()).decode("ascii")

