from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**data, "updated_at": utc_now_iso()}
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2))
    temp_path.replace(path)


def read_status(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text())
