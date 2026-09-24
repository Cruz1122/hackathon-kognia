"""Create the initial SUPERADMIN explicitly: python -m app.auth.bootstrap.

Pass --demo-only to create just the demo organization and ADMIN, without
requiring SUPERADMIN_EMAIL/SUPERADMIN_PASSWORD.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db.models import Organization, User, UserRole
from ..db.session import dispose_engine, get_session_factory
from .passwords import hash_password
from .schemas import normalize_email, normalize_name, normalize_slug


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
            created = False
        else:
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
            created = True
    print("SUPERADMIN created." if created else "SUPERADMIN already exists.")
    await _bootstrap_demo_admin()
    return 0


async def _bootstrap_demo_admin(*, required: bool = False) -> None:
    email = os.getenv("DEMO_ADMIN_EMAIL", "").strip()
    password = os.getenv("DEMO_ADMIN_PASSWORD", "")
    if not email or not password:
        if required:
            raise RuntimeError("DEMO_ADMIN_EMAIL and DEMO_ADMIN_PASSWORD are required.")
        return
    email = normalize_email(email)
    org_name = normalize_name(os.getenv("DEMO_ORG_NAME", "Demo Kognia") or "Demo Kognia")
    org_slug = normalize_slug(os.getenv("DEMO_ORG_SLUG", "demo-kognia") or "demo-kognia")
    async with get_session_factory()() as session:
        organization = await session.scalar(select(Organization).where(Organization.slug == org_slug))
        if organization is None:
            organization = Organization(name=org_name, slug=org_slug)
            session.add(organization)
            await session.flush()
        existing = await session.scalar(select(User).where(User.email == email))
        if existing is not None:
            if existing.role != UserRole.ADMIN or existing.organization_id != organization.id:
                raise RuntimeError("A user with the demo admin email already exists.")
            print("DEMO ADMIN already exists.")
            return
        session.add(
            User(
                email=email,
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
                organization_id=organization.id,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise RuntimeError("Bootstrap could not create the DEMO ADMIN.") from exc
    print("DEMO ADMIN created.")


async def _run_bootstrap(*, demo_only: bool = False) -> int:
    try:
        if demo_only:
            await _bootstrap_demo_admin(required=True)
            return 0
        return await _bootstrap()
    finally:
        await dispose_engine()


def main() -> int:
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(Path.cwd() / "backend" / ".env", override=False)
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    demo_only = "--demo-only" in sys.argv[1:]
    try:
        return asyncio.run(_run_bootstrap(demo_only=demo_only))
    except Exception:
        print("Bootstrap failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
