from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args: list[str]) -> None:
    process = subprocess.run(args, capture_output=True, text=True)
    if process.returncode != 0:
        raise FFmpegError(process.stderr.strip() or process.stdout.strip())


def ffprobe_duration(path: Path) -> float:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(args, capture_output=True, text=True)
    if process.returncode != 0:
        raise FFmpegError(process.stderr.strip() or process.stdout.strip())
    payload: dict[str, Any] = json.loads(process.stdout)
    duration = payload.get("format", {}).get("duration")
    if duration is None:
        raise FFmpegError(f"Missing duration for {path}")
    return float(duration)
