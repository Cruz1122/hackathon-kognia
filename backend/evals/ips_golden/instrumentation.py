"""Best-effort benchmark instrumentation around existing runtime boundaries."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.platform.tracing import TraceRecorder

from .models import TurnPlan
from .snapshot import SnapshotView


class RuntimeInstrumentation:
    """Patch only evaluation-local call boundaries and restore them afterwards.

    The product tools and JEV functions remain unchanged.  The wrappers reuse
    the turn's existing :class:`TraceRecorder`, use monotonic durations through
    its ``span`` method, and never store credentials or raw provider payloads.
    """

    def __init__(
        self,
        recorder: TraceRecorder,
        snapshot: SnapshotView,
        turn: TurnPlan,
        *,
        offline: bool,
    ) -> None:
        self.recorder = recorder
        self.snapshot = snapshot
        self.turn = turn
        self.offline = offline
        self._patches: list[tuple[Any, str, Any]] = []
        self._seen: set[str] = set()
        self._failure_used: set[str] = set()

    def _patch(self, target: Any, name: str, replacement: Any) -> None:
        self._patches.append((target, name, getattr(target, name)))
        setattr(target, name, replacement)

    def _cache_state(self, operation: str, arguments: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        try:
            encoded = json.dumps([operation, arguments, kwargs], sort_keys=True, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            encoded = repr((operation, arguments, kwargs))
        key = hashlib.sha256(encoded.encode()).hexdigest()
        if key in self._seen:
            return "hot"
        self._seen.add(key)
        return "cold"

    async def _call_with_spans(
        self,
        operation: str,
        original: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        cache_state = self._cache_state(operation, args, kwargs)
        retrieval = operation in {"search", "semantic_search", "sites_by_ids"}
        outer = self.recorder.span(
            "ips.retrieval" if retrieval else "ips.repository",
            {"operation": operation, "cache": cache_state, "snapshot_id": self.snapshot.metadata.snapshot_id},
        )
        inner = self.recorder.span(
            f"postgres.ips.{operation}",
            {"cache": cache_state, "snapshot_id": self.snapshot.metadata.snapshot_id},
        )
        try:
            result = await original(*args, **kwargs)
        except Exception as exc:
            self.recorder.close_span(inner, ok=False, error=type(exc).__name__)
            self.recorder.close_span(outer, ok=False, error=type(exc).__name__)
            raise
        else:
            self.recorder.close_span(inner, ok=True)
            self.recorder.close_span(outer, ok=True)
            return result

    def _wrap_repository(self, repository: Any, name: str) -> None:
        original = getattr(repository, name)

        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            if self.turn.failure_mode == "postgres_transient" and name == "search" and name not in self._failure_used:
                self._failure_used.add(name)
                outer = self.recorder.span("ips.retrieval", {"operation": name, "cache": "cold", "injected_failure": True})
                inner = self.recorder.span("postgres.ips.search", {"cache": "cold", "injected_failure": True})
                self.recorder.close_span(inner, ok=False, error="TransientPostgresFailure")
                self.recorder.close_span(outer, ok=False, error="TransientPostgresFailure")
                raise RuntimeError("Golden injected transient PostgreSQL failure")
            if self.turn.failure_mode == "no_active_snapshot" and name == "active_snapshot":
                cache_state = self._cache_state(name, args, kwargs)
                span = self.recorder.span("postgres.ips.active_snapshot", {"cache": cache_state, "injected_failure": True})
                self.recorder.close_span(span, ok=False, error="NoActiveSnapshot")
                return None
            return await self._call_with_spans(name, original, *args, **kwargs)

        self._patch(repository, name, wrapped)

    def _wrap_vector_store(self, vector_store: Any) -> None:
        original = vector_store.search

        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            cache_state = self._cache_state("chroma.search", args, kwargs)
            span = self.recorder.span(
                "chroma.ips.search",
                {
                    "cache": cache_state,
                    "provider": "fake" if self.offline else "chroma",
                    "snapshot_id": self.snapshot.metadata.snapshot_id,
                },
            )
            if self.turn.failure_mode == "chroma_error":
                self.recorder.close_span(span, ok=False, error="InjectedChromaFailure")
                raise RuntimeError("Golden injected Chroma failure")
            if self.offline:
                from app.domains.ips.vector_store import IPSSemanticHit, semantic_document

                hits: list[IPSSemanticHit] = []
                for rank, code in enumerate(self.turn.relevant_site_codes):
                    site = self.snapshot.get(code)
                    if site is None:
                        continue
                    content = semantic_document(_as_normalized_site(site))
                    hits.append(
                        IPSSemanticHit(
                            site_id=site.site_id,
                            site_code=site.site_code,
                            content=content,
                            score=max(0.01, 0.99 - rank * 0.05),
                            metadata={
                                "snapshot_id": site.snapshot_id,
                                "site_id": site.site_id,
                                "site_code": site.site_code,
                                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                                "cutoff": site.cutoff,
                            },
                        )
                    )
                self.recorder.close_span(span, ok=True, hits=len(hits))
                return hits
            try:
                result = await original(*args, **kwargs)
            except Exception as exc:
                self.recorder.close_span(span, ok=False, error=type(exc).__name__)
                raise
            self.recorder.close_span(span, ok=True, hits=len(result or []))
            return result

        self._patch(vector_store, "search", wrapped)

    def install(self) -> "RuntimeInstrumentation":
        from app.domains.ips import tools as ips_tools

        repository = getattr(ips_tools, "repository", None)
        if repository is not None:
            for name in (
                "active_snapshot",
                "search",
                "canonical_location",
                "details",
                "capacities",
                "compare_capacity",
                "sites_by_ids",
            ):
                self._wrap_repository(repository, name)
        vector_store = getattr(ips_tools, "vector_store", None)
        if vector_store is not None:
            self._wrap_vector_store(vector_store)
        return self

    def close(self) -> None:
        for target, name, original in reversed(self._patches):
            setattr(target, name, original)
        self._patches.clear()

    def __enter__(self) -> "RuntimeInstrumentation":
        return self.install()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def _as_normalized_site(site: Any) -> Any:
    """Adapt a snapshot record to the existing semantic-document contract."""

    from types import SimpleNamespace

    return SimpleNamespace(
        site_name=site.site_name,
        provider_name=site.provider_name,
        site_code=site.site_code,
        municipality=site.municipality,
        department=site.department,
        nature=site.nature,
        care_level=site.care_level,
        address=site.address,
        phone=site.phone,
        capacities=[
            SimpleNamespace(group_name=item.group, description=item.description, quantity=item.registered_quantity)
            for item in site.capacities
        ],
    )


__all__ = ["RuntimeInstrumentation"]
