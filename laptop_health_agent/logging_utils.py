from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import LOG_PATH, ensure_data_dir


def audit(event: str, payload: dict[str, Any]) -> None:
    ensure_data_dir()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "payload": payload,
    }
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")
