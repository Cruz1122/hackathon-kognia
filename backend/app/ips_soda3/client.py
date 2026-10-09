"""Safe async SODA3 client for the fixed IPS dataset."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SODA3_URL = "https://www.datos.gov.co/api/v3/views/s2ru-bqt6/query.json"
DATASET_ID = "s2ru-bqt6"


class Soda3Error(RuntimeError):
    """Controlled upstream failure without exposing the upstream response body."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient


class Soda3Client:
    """POST-only client that accepts only internally generated SELECT queries."""

    def __init__(
        self,
        *,
        app_token: str,
        http: httpx.AsyncClient,
        timeout: float = 10.0,
        max_attempts: int = 3,
    ) -> None:
        if not app_token.strip():
            raise ValueError("SODA3 requiere un X-App-Token válido")
        if timeout <= 0:
            raise ValueError("timeout debe ser positivo")
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts debe estar entre 1 y 5")
        self._token = app_token.strip()
        self._http = http
        self._timeout = timeout
        self._max_attempts = max_attempts

    @staticmethod
    def payload(
        query: str,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        if not query.startswith("SELECT "):
            raise ValueError("Se admiten únicamente consultas SELECT generadas internamente")
        payload: dict[str, Any] = {
            "query": query,
            "includeSynthetic": False,
            "includeSystem": False,
        }
        if page is not None:
            if page < 1 or page_size is None or not 1 <= page_size <= 1000:
                raise ValueError("Paginación inválida")
            payload["page"] = {"pageNumber": page, "pageSize": page_size}
        elif page_size is not None:
            raise ValueError("page_size sin page")
        return payload

    async def query(
        self,
        query: str,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        body = self.payload(query, page, page_size)
        for attempt in range(self._max_attempts):
            started = time.perf_counter()
            try:
                response = await self._http.post(
                    SODA3_URL,
                    headers={
                        "X-App-Token": self._token,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=self._timeout,
                )
                logger.info(
                    "SODA3 status=%s latency_ms=%.0f attempt=%s",
                    response.status_code,
                    (time.perf_counter() - started) * 1000,
                    attempt + 1,
                )
                if response.status_code >= 400:
                    transient = response.status_code == 429 or 500 <= response.status_code <= 599
                    if transient and attempt + 1 < self._max_attempts:
                        await asyncio.sleep(self._retry_delay(response, attempt))
                        continue
                    raise Soda3Error(
                        f"SODA3 respondió HTTP {response.status_code}",
                        status_code=response.status_code,
                        transient=transient,
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise Soda3Error("SODA3 devolvió JSON inválido", transient=True) from exc
                if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
                    raise Soda3Error(
                        "SODA3 devolvió un formato de filas inesperado",
                        transient=True,
                    )
                return data
            except httpx.RequestError as exc:
                logger.warning(
                    "SODA3 transporte falló: %s intento=%s",
                    type(exc).__name__,
                    attempt + 1,
                )
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(min(0.25 * 2**attempt, 2.0))
                    continue
                raise Soda3Error("SODA3 no está disponible", transient=True) from exc
        raise AssertionError("El ciclo de reintentos terminó sin retorno")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            delay = float(retry_after)
        except ValueError:
            delay = 0.25 * 2**attempt
        return min(max(delay, 0.0), 2.0)


__all__ = ["DATASET_ID", "SODA3_URL", "Soda3Client", "Soda3Error"]
