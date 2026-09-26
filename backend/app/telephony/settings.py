from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def telnyx_enabled() -> bool:
    return os.getenv("TELNYX_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TelnyxSettings:
    api_key: str
    public_key: str
    connection_id: str
    phone_number: str
    webhook_host: str
    organization_id: str
    system_user_id: str
    recordings_dir: Path
    capture_dir: Path
    replay_window_seconds: int = 300


def load_settings() -> TelnyxSettings:
    host = os.getenv("TELNYX_WEBHOOK_HOST", "corner-gorged-calamari.ngrok-free.dev").strip()
    host = host.removeprefix("https://").removeprefix("http://").strip("/")
    recordings = Path(os.getenv("TELNYX_RECORDINGS_DIR", "/var/lib/kognia/recordings"))
    capture = Path(os.getenv("TELNYX_CAPTURE_DIR", "/tmp/telnyx-captures"))
    return TelnyxSettings(
        api_key=os.getenv("TELNYX_API_KEY", "").strip(),
        public_key=os.getenv("TELNYX_PUBLIC_KEY", "").strip(),
        connection_id=os.getenv("TELNYX_CONNECTION_ID", "").strip(),
        phone_number=os.getenv("TELNYX_PHONE_NUMBER", "").strip(),
        webhook_host=host,
        organization_id=os.getenv("TELNYX_ORGANIZATION_ID", "").strip(),
        system_user_id=os.getenv("TELNYX_SYSTEM_USER_ID", "").strip(),
        recordings_dir=recordings,
        capture_dir=capture,
    )
