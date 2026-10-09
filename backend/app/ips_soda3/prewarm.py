"""Administrative Redis prewarm for the IPS SODA3 adapter.

Run from the repository root with::

    cd backend && .venv/bin/python -m app.ips_soda3.prewarm --all-pages

This command is intentionally separate from the voice request path.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from redis.asyncio import Redis

from .cache import RedisCache
from .client import Soda3Client
from .config import IPSSettings
from .service import IPSService


def _load_repository_environment() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    load_dotenv(repository_root / ".env", override=False)
    load_dotenv(repository_root / "backend" / ".env", override=False)


async def warm(*, all_pages: bool) -> None:
    _load_repository_environment()
    settings = IPSSettings.from_env()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.ping()
        async with httpx.AsyncClient() as http:
            service = IPSService(
                Soda3Client(
                    app_token=settings.app_token,
                    http=http,
                    timeout=settings.http_timeout,
                    max_attempts=settings.max_attempts,
                ),
                RedisCache(redis),
                ttl=settings.cache_ttl,
                empty_ttl=settings.empty_ttl,
                stale_ttl=settings.stale_ttl,
            )
            count = await service.count_records()
            print(f"Registros reportados por SODA3: {count['total_registros']}")
            first = await service.list_ips(page_size=50)
            print(f"Primera página precargada: {len(first['data'])} filas")
            if all_pages:
                total = 0
                async for page in service.list_all_pages(page_size=1000, max_pages=100):
                    total += len(page["data"])
                    print(f"Página {page['page']} precargada; acumulado={total}")
                print(f"Precarga por páginas terminada: {total} filas")
            print("Las consultas filtradas se cachean cuando se ejecutan por primera vez.")
    finally:
        await redis.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Precalienta consultas IPS SODA3 en Redis")
    parser.add_argument(
        "--all-pages",
        action="store_true",
        help="Precalentar también todas las páginas hasta 1000 filas por página",
    )
    args = parser.parse_args()
    try:
        asyncio.run(warm(all_pages=args.all_pages))
    except Exception as exc:
        print(f"Fallo de precalentamiento: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
