from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SongSegment:
    start_s: float
    end_s: float
    method: str
    snap: str
    loop_audio: bool


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _nearest_beat(beat_times: list[float], target: float) -> tuple[Optional[float], float]:
    if not beat_times:
        return None, float("inf")
    # simple linear scan is fine for short lists
    best = min(beat_times, key=lambda beat: abs(beat - target))
    return best, abs(best - target)


def select_song_segment(
    song_path: Path,
    target_length_s: float,
    method: str,
    min_start_s: float,
    snap_to: str,
    beats_full: Optional[Dict[str, Any]] = None,
) -> SongSegment:
    try:
        import librosa
        import numpy as np
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(f"librosa not available: {exc}")

    method = (method or "auto_energy").lower()
    snap_to = (snap_to or "downbeat").lower()

    y, sr = librosa.load(str(song_path), sr=22050, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    loop_audio = duration < target_length_s + 0.25
    if loop_audio:
        return SongSegment(
            start_s=0.0,
            end_s=max(duration, target_length_s),
            method=method,
            snap=snap_to,
            loop_audio=True,
        )

    start_s = 0.0
    if method == "manual":
        start_s = min_start_s
    else:
        # Auto energy selection
        hop_length = int(0.5 * sr)
        frame_length = hop_length
        rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
        frame_times = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=hop_length)
        if len(rms) == 0:
            start_s = 0.0
        else:
            window_frames = max(1, int(target_length_s / 0.5))
            window = np.ones(window_frames, dtype=np.float32)
            energy = np.convolve(rms, window, mode="valid")
            best_index = int(np.argmax(energy)) if len(energy) > 0 else 0
            start_s = float(frame_times[best_index])

    max_start = max(0.0, duration - target_length_s)
    start_s = _clamp(float(start_s), float(min_start_s), max_start)

    # Snap start to nearest beat/downbeat within 0.5s
    if beats_full and snap_to in {"downbeat", "beat"}:
        beat_key = "downbeats" if snap_to == "downbeat" else "beats"
        beat_times = beats_full.get(beat_key, []) if isinstance(beats_full, dict) else []
        beat, diff = _nearest_beat(beat_times, start_s)
        if beat is not None and diff <= 0.5:
            start_s = _clamp(float(beat), float(min_start_s), max_start)

    return SongSegment(
        start_s=round(start_s, 3),
        end_s=round(start_s + target_length_s, 3),
        method=method,
        snap=snap_to,
        loop_audio=False,
    )


def slice_beats(beats_full: Dict[str, Any], start_s: float, end_s: float) -> Dict[str, Any]:
    beats = beats_full.get("beats", []) if isinstance(beats_full, dict) else []
    downbeats = beats_full.get("downbeats", []) if isinstance(beats_full, dict) else []
    tempo = beats_full.get("tempo") if isinstance(beats_full, dict) else None

    def _slice(values: list[float]) -> list[float]:
        return [round(t - start_s, 3) for t in values if start_s <= t <= end_s]

    return {
        "tempo": tempo,
        "beats": _slice(beats),
        "downbeats": _slice(downbeats),
        "segment_start_s": round(start_s, 3),
        "segment_end_s": round(end_s, 3),
    }
