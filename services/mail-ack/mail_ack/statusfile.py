"""Local status record. This is not an external alert channel."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path


def write_status(path: str, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["alert_channel"] = "local_status_only"
    body["written_at"] = datetime.now(timezone.utc).isoformat()
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)
