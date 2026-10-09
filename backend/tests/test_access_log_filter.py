import logging

import pytest

from app import logging_config
from app.logging_config import install_access_log_filter


def make_record(full_path: str, method: str = "GET", status: int = 200) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("172.19.0.1:1234", method, full_path, "1.1", status),
        exc_info=None,
    )


def silence_filter() -> logging.Filter:
    return logging_config._SilencePollingAccessLog()


@pytest.mark.parametrize("path", ["/calls", "/calls?limit=5", "/auth/me", "/auth/me?x=1"])
def test_polling_paths_are_silenced(path: str) -> None:
    assert silence_filter().filter(make_record(path)) is False


@pytest.mark.parametrize(
    "path",
    ["/calls/abc", "/conversations/xyz", "/analytics/dashboard", "/health/ready", "/auth/login", "/ask"],
)
def test_other_paths_pass_through(path: str) -> None:
    assert silence_filter().filter(make_record(path)) is True


def test_malformed_args_pass_through() -> None:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "request", (), None)
    assert silence_filter().filter(record) is True


def test_install_is_idempotent() -> None:
    target = logging.getLogger("uvicorn.access")
    original = list(target.filters)
    try:
        install_access_log_filter()
        install_access_log_filter()
        added = [
            item for item in target.filters if isinstance(item, logging_config._SilencePollingAccessLog)
        ]
        assert len(added) == 1
    finally:
        target.filters = original
