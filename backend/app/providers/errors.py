from __future__ import annotations


class ProviderError(RuntimeError):
    """An upstream provider failed without exposing its response body."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable
