from __future__ import annotations

import base64
import time

import nacl.exceptions
import nacl.signing


class SignatureError(Exception):
    pass


def _verify_key(public_key: str) -> nacl.signing.VerifyKey:
    raw = base64.b64decode(public_key)
    if len(raw) != 32:
        raw = raw[-32:]
    return nacl.signing.VerifyKey(raw)


def verify_telnyx_signature(
    *,
    raw_body: bytes,
    signature: str | None,
    timestamp: str | None,
    public_key: str,
    now: float | None = None,
    window_seconds: int = 300,
) -> None:
    if not signature or not timestamp or not public_key:
        raise SignatureError("missing signature")
    try:
        issued_at = int(timestamp)
    except ValueError as exc:
        raise SignatureError("invalid timestamp") from exc
    current = int(time.time() if now is None else now)
    if abs(current - issued_at) > window_seconds:
        raise SignatureError("stale timestamp")
    try:
        key = _verify_key(public_key)
        key.verify(timestamp.encode("ascii") + b"|" + raw_body, base64.b64decode(signature))
    except (nacl.exceptions.BadSignatureError, ValueError) as exc:
        raise SignatureError("invalid signature") from exc
