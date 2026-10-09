"""Explicit deployment command for the official IPS snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

from ...db.session import dispose_engine, get_session_factory
from ...ips_soda3.cache import InMemoryCache
from ...ips_soda3.client import Soda3Client
from ...ips_soda3.config import IPSSettings
from ...ips_soda3.service import IPSService
from .ingestion import IPSIngestionService
from .repository import IPSRepository
from .vector_store import IPSVectorStore


def _load_environment() -> None:
    root = Path(__file__).resolve().parents[4]
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / "backend" / ".env", override=False)


async def run(*, page_size: int, max_pages: int) -> dict:
    _load_environment()
    settings = IPSSettings.from_env()
    async with httpx.AsyncClient() as http:
        source = IPSService(
            Soda3Client(
                app_token=settings.app_token,
                http=http,
                timeout=settings.http_timeout,
                max_attempts=settings.max_attempts,
            ),
            InMemoryCache(),
            ttl=settings.cache_ttl,
            empty_ttl=settings.empty_ttl,
            stale_ttl=settings.stale_ttl,
        )
        service = IPSIngestionService(
            IPSRepository(get_session_factory()),
            IPSVectorStore(),
        )
        return await service.ingest_from_source(
            source,
            page_size=page_size,
            max_pages=max_pages,
        )


async def _main(page_size: int, max_pages: int) -> int:
    try:
        result = await run(page_size=page_size, max_pages=max_pages)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"IPS ingestion failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest official IPS data from SODA3 into PostgreSQL")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.page_size <= 1000 or not 1 <= args.max_pages <= 1000:
        parser.error("page-size and max-pages must be between 1 and 1000")
    raise SystemExit(asyncio.run(_main(args.page_size, args.max_pages)))


if __name__ == "__main__":
    main()
