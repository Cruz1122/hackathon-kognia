from __future__ import annotations

import logging

# High-frequency polling endpoints whose access-log lines are pure noise.
SILENCED_ACCESS_LOG_PATHS = frozenset({"/calls", "/auth/me"})


class _SilencePollingAccessLog(logging.Filter):
    """Drop uvicorn access records for high-frequency polling endpoints.

    Uvicorn emits access records as ``logger.info(fmt, client, method,
    full_path, http_version, status)``. Any unexpected shape falls through so
    logging never breaks: the record is allowed unless it clearly matches a
    silenced path.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        full_path = args[2]
        if not isinstance(full_path, str):
            return True
        return full_path.split("?", 1)[0] not in SILENCED_ACCESS_LOG_PATHS


def install_access_log_filter() -> None:
    """Attach the polling filter to uvicorn's access logger exactly once."""
    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(existing, _SilencePollingAccessLog) for existing in logger.filters):
        return
    logger.addFilter(_SilencePollingAccessLog())
