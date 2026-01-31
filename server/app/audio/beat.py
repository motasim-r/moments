import json
from pathlib import Path
from typing import Any, Dict


def detect_beats(song_path: Path) -> Dict[str, Any]:
    try:
        import librosa
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(f"librosa not available: {exc}")

    y, sr = librosa.load(str(song_path), sr=22050, mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    downbeats = beat_times[::4]
    return {
        "tempo": float(tempo),
        "beats": [round(t, 3) for t in beat_times],
        "downbeats": [round(t, 3) for t in downbeats],
    }


def write_beats(path: Path, beats: Dict[str, Any]) -> None:
    path.write_text(json.dumps(beats, indent=2))
