"""Create the initial SUPERADMIN explicitly: python -m app.auth.bootstrap."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db.models import User, UserRole
from ..db.session import dispose_engine, get_session_factory
from .passwords import hash_password
from .schemas import normalize_email


def _required_environment() -> tuple[str, str]:
    email = os.getenv("SUPERADMIN_EMAIL", "").strip()
    password = os.getenv("SUPERADMIN_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("Required bootstrap configuration is missing.")
    return normalize_email(email), password


async def _bootstrap() -> int:
    email, password = _required_environment()
    async with get_session_factory()() as session:
        existing = await session.scalar(select(User).where(User.email == email))
        if existing is not None:
            if existing.role != UserRole.SUPERADMIN or existing.organization_id is not None:
                raise RuntimeError("A user with the bootstrap email already exists.")
            print("SUPERADMIN already exists.")
            return 0
        session.add(
            User(
                email=email,
                password_hash=hash_password(password),
                role=UserRole.SUPERADMIN,
                organization_id=None,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise RuntimeError("Bootstrap could not create the SUPERADMIN.") from exc
    print("SUPERADMIN created.")
    return 0


async def _run_bootstrap() -> int:
    try:
        return await _bootstrap()
    finally:
        await dispose_engine()


def main() -> int:
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(Path.cwd() / "backend" / ".env", override=False)
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    try:
        return asyncio.run(_run_bootstrap())
    except Exception:
        print("SUPERADMIN bootstrap failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
