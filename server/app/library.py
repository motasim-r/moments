from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.utils.paths import ROOT_DIR

LIBRARY_DIR = ROOT_DIR / "library" / "glasses"


def ensure_library_dir() -> Path:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    return LIBRARY_DIR


def unique_library_name(name: Optional[str]) -> str:
    ensure_library_dir()
    safe_name = Path(name or "").name
    if not safe_name:
        safe_name = f"clip_{uuid.uuid4().hex[:8]}.mp4"
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    candidate = safe_name
    counter = 1
    while (LIBRARY_DIR / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def store_library_file(source: Path, name: Optional[str] = None) -> Path:
    destination = ensure_library_dir() / unique_library_name(name or source.name)
    shutil.copy2(source, destination)
    return destination


def list_library_files() -> list[dict[str, Any]]:
    ensure_library_dir()
    items: list[dict[str, Any]] = []
    for path in sorted(LIBRARY_DIR.iterdir()):
        if not path.is_file():
            continue
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    return items


def resolve_library_file(name: str) -> Path:
    candidate = ensure_library_dir() / name
    if not candidate.exists():
        raise FileNotFoundError(name)
    return candidate
