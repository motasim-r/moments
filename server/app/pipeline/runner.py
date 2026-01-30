from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.utils.ffmpeg import FFmpegError, ffprobe_duration, run_ffmpeg
from app.utils.ntsc import run_ntsc_cli
from app.utils.paths import JobPaths, ROOT_DIR, ensure_job_dirs
from app.utils.status import write_status


@dataclass
class ClipInput:
    clip_id: str
    path: Path
    original_name: str


@dataclass
class ProxyClip:
    clip_id: str
    path: Path
    duration: float


DEFAULT_SETTINGS = {
    "target_length_s": 15,
    "vibe": "hype",
    "vhs_intensity": 0.7,
    "glitch_amount": 0.2,
    "grain_amount": 0.5,
    "vhs_engine": "ntsc-rs",
    "ntsc_preset": "custom",
    "resolution": "1080x1920",
    "fps": 30,
    "seed": None,
    "include_clip_audio": False,
    "locked_clips": [],
}

NTSC_PRESETS = {
    "custom": ROOT_DIR / "presets" / "ntsc" / "ntsc_custom.json",
    "semi-sharp": ROOT_DIR / "presets" / "ntsc" / "ntsc_semi_sharp.json",
    "game-tape": ROOT_DIR / "presets" / "ntsc" / "ntsc_game_tape.json",
}


def _resolve_seed(settings: dict[str, Any]) -> int:
    seed = settings.get("seed")
    if seed is None:
        seed = uuid.uuid4().int % 1_000_000
    return int(seed)


def _update_status(paths: JobPaths, payload: dict[str, Any]) -> None:
    write_status(paths.status_path, payload)


def _vibe_segment_base(vibe: str) -> float:
    vibe = (vibe or "").lower()
    if vibe == "chill":
        return 1.4
    if vibe == "chaotic":
        return 0.6
    return 0.9


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _resolve_ntsc_preset(settings: dict[str, Any]) -> Path:
    preset_name = settings.get("ntsc_preset", "custom")
    if isinstance(preset_name, str):
        preset_path = NTSC_PRESETS.get(preset_name)
        if preset_path and preset_path.exists():
            return preset_path

    intensity = float(settings.get("vhs_intensity", 0.7))
    preset_path = NTSC_PRESETS["semi-sharp"] if intensity < 0.45 else NTSC_PRESETS["game-tape"]
    if not preset_path.exists():
        raise RuntimeError(f"Missing ntsc-rs preset file: {preset_path}")
    return preset_path


def _load_preset(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _write_preset(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2))


def _build_ntsc_segments(total_length: float, rng: random.Random) -> list[tuple[float, float]]:
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    min_len = 1.6
    max_len = 4.0
    while cursor < total_length - 0.01:
        seg_len = rng.uniform(min_len, max_len)
        end = min(total_length, cursor + seg_len)
        segments.append((round(cursor, 3), round(end, 3)))
        cursor = end
    return segments


def _render_dynamic_ntsc(
    paths: JobPaths,
    base_path: Path,
    target_length: float,
    rng: random.Random,
) -> Path:
    segment_dir = paths.output_dir / "ntsc_segments"
    segment_dir.mkdir(parents=True, exist_ok=True)

    preset_paths = [
        NTSC_PRESETS["custom"],
        NTSC_PRESETS["semi-sharp"],
        NTSC_PRESETS["game-tape"],
    ]

    segments = _build_ntsc_segments(target_length, rng)
    ntsc_segments: list[Path] = []

    for idx, (start, end) in enumerate(segments, start=1):
        segment_path = segment_dir / f"seg_{idx:02d}.mp4"
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                str(base_path),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-an",
                str(segment_path),
            ]
        )

        preset_source = rng.choice(preset_paths)
        preset_payload = _load_preset(preset_source)
        preset_payload["random_seed"] = rng.randint(0, 2**31 - 1)
        preset_path = segment_dir / f"preset_{idx:02d}.json"
        _write_preset(preset_path, preset_payload)

        ntsc_segment = segment_dir / f"seg_{idx:02d}_ntsc.mp4"
        run_ntsc_cli(segment_path, ntsc_segment, preset_path)
        ntsc_segments.append(ntsc_segment)

    concat_list = segment_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{path.as_posix()}'" for path in ntsc_segments))

    ntsc_path = paths.output_dir / "ntsc.mp4"
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-an",
            str(ntsc_path),
        ]
    )

    return ntsc_path


def preprocess_clips(paths: JobPaths, clips: list[ClipInput]) -> list[ProxyClip]:
    proxies: list[ProxyClip] = []
    for clip in clips:
        proxy_path = paths.proxy_dir / f"{clip.clip_id}.mp4"
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(clip.path),
            "-vf",
            "scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-an",
            str(proxy_path),
        ]
        run_ffmpeg(args)
        duration = ffprobe_duration(proxy_path)
        proxies.append(ProxyClip(clip_id=clip.clip_id, path=proxy_path, duration=duration))
    return proxies


def build_edl(paths: JobPaths, proxies: list[ProxyClip], settings: dict[str, Any]) -> dict[str, Any]:
    target_length = float(settings.get("target_length_s", 15))
    vibe = settings.get("vibe", "hype")
    seed = _resolve_seed(settings)
    rng = random.Random(seed)

    base = _vibe_segment_base(vibe)
    timeline: list[dict[str, Any]] = []
    remaining = target_length

    if not proxies:
        raise RuntimeError("No proxies to build EDL")

    clip_index = 0
    safety = 0
    while remaining > 0.01 and safety < 500:
        safety += 1
        proxy = proxies[clip_index % len(proxies)]
        jitter = rng.uniform(-0.2, 0.2)
        seg_len = _clamp(base + jitter, 0.5, 2.0)
        seg_len = min(seg_len, remaining)

        if proxy.duration <= seg_len + 0.05:
            start = 0.0
            end = min(proxy.duration, seg_len)
        else:
            max_start = proxy.duration - seg_len
            start = rng.uniform(0.0, max_start)
            end = start + seg_len

        timeline.append(
            {
                "clip_id": proxy.clip_id,
                "in": round(start, 3),
                "out": round(end, 3),
                "transition": "cut",
            }
        )
        remaining -= seg_len
        clip_index += 1

    edl = {
        "version": "0.1",
        "settings": {
            "target_length_s": target_length,
            "resolution": settings.get("resolution", "1080x1920"),
            "fps": settings.get("fps", 30),
            "vibe": vibe,
            "seed": seed,
        },
        "timeline": timeline,
        "effects": {
            "vhs_intensity": settings.get("vhs_intensity", 0.7),
            "glitch_amount": settings.get("glitch_amount", 0.2),
            "grain_amount": settings.get("grain_amount", 0.5),
        },
    }
    paths.edl_path.write_text(json.dumps(edl, indent=2))
    return edl


def _vhs_filter(intensity: float) -> str:
    intensity = _clamp(float(intensity), 0.0, 1.0)
    noise = 6 + intensity * 18
    chroma = 1 + intensity * 3
    contrast = 1.0 + intensity * 0.08
    saturation = 1.0 + intensity * 0.2
    scan_alpha = 0.05 + intensity * 0.18

    return (
        f"noise=alls={noise}:allf=t+u,"
        f"eq=contrast={contrast}:saturation={saturation}:brightness=0.02,"
        f"chromashift=crh={chroma}:crv={chroma},"
        f"drawgrid=width=iw:height=3:thickness=1:color=black@{scan_alpha}"
    )


def _resolve_resolution(settings: dict[str, Any]) -> tuple[int, int]:
    raw = settings.get("resolution", "1080x1920")
    if isinstance(raw, str) and "x" in raw:
        width_str, height_str = raw.lower().split("x", 1)
        try:
            width = int(width_str)
            height = int(height_str)
            return width, height
        except ValueError:
            pass
    return (1080, 1920)


def render_reel(
    paths: JobPaths,
    proxies: list[ProxyClip],
    edl: dict[str, Any],
    song_path: Path,
    settings: dict[str, Any],
) -> dict[str, Path]:
    timeline = edl["timeline"]
    if not timeline:
        raise RuntimeError("Empty timeline")

    width, height = _resolve_resolution(settings)
    fps = int(settings.get("fps", 30))
    target_length = float(edl["settings"]["target_length_s"])

    inputs: list[str] = []
    for segment in timeline:
        clip_id = segment["clip_id"]
        proxy = next((p for p in proxies if p.clip_id == clip_id), None)
        if proxy is None:
            raise RuntimeError(f"Missing proxy for {clip_id}")
        inputs.extend(["-i", str(proxy.path)])

    inputs.extend(["-stream_loop", "-1", "-i", str(song_path)])

    filter_parts: list[str] = []
    for index, segment in enumerate(timeline):
        start = segment["in"]
        end = segment["out"]
        filter_parts.append(
            f"[{index}:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]"
        )

    concat_inputs = "".join(f"[v{idx}]" for idx in range(len(timeline)))
    filter_parts.append(f"{concat_inputs}concat=n={len(timeline)}:v=1:a=0[vcat]")
    filter_parts.append(
        f"[vcat]fps={fps},scale={width}:{height}:flags=lanczos,format=yuv420p[vbase]"
    )
    audio_index = len(timeline)
    filter_parts.append(
        f"[{audio_index}:a]atrim=0:{target_length},asetpts=PTS-STARTPTS[aout]"
    )

    filter_complex = ";".join(filter_parts)

    base_path = paths.output_dir / "base.mp4"
    args_base = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vbase]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(base_path),
    ]
    run_ffmpeg(args_base)

    final_path = paths.output_dir / "final.mp4"
    vhs_engine = str(settings.get("vhs_engine", "ntsc-rs")).lower()

    if vhs_engine == "ntsc-rs":
        ntsc_mode = str(settings.get("ntsc_preset", "custom")).lower()
        if ntsc_mode == "dynamic":
            seed = int(edl["settings"].get("seed", 0))
            rng = random.Random(seed + 77)
            ntsc_path = _render_dynamic_ntsc(paths, base_path, target_length, rng)
        else:
            preset_path = _resolve_ntsc_preset(settings)
            ntsc_path = paths.output_dir / "ntsc.mp4"
            run_ntsc_cli(base_path, ntsc_path, preset_path)

        try:
            run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(ntsc_path),
                    "-i",
                    str(base_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(final_path),
                ]
            )
        except FFmpegError:
            run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(ntsc_path),
                    "-i",
                    str(base_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(final_path),
                ]
            )
    else:
        vhs = _vhs_filter(settings.get("vhs_intensity", 0.7))
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(base_path),
                "-vf",
                f"{vhs},format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                "-shortest",
                str(final_path),
            ]
        )

    preview_path = paths.output_dir / "preview.mp4"
    args_preview = [
        "ffmpeg",
        "-y",
        "-i",
        str(final_path),
        "-vf",
        "scale=720:1280:flags=lanczos",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(preview_path),
    ]
    run_ffmpeg(args_preview)

    return {"final": final_path, "preview": preview_path}


def run_job(job_id: str, clips: list[ClipInput], song_path: Path, settings: dict[str, Any]) -> None:
    paths = ensure_job_dirs(job_id)
    settings = {**DEFAULT_SETTINGS, **settings}

    _update_status(
        paths,
        {
            "job_id": job_id,
            "status": "running",
            "step": "preprocess",
            "progress": 0.1,
            "message": "Generating proxies",
        },
    )

    try:
        proxies = preprocess_clips(paths, clips)

        _update_status(
            paths,
            {
                "job_id": job_id,
                "status": "running",
                "step": "edl",
                "progress": 0.45,
                "message": "Building edit decision list",
            },
        )

        edl = build_edl(paths, proxies, settings)

        _update_status(
            paths,
            {
                "job_id": job_id,
                "status": "running",
                "step": "render",
                "progress": 0.7,
                "message": "Rendering reel",
            },
        )

        outputs = render_reel(paths, proxies, edl, song_path, settings)

        _update_status(
            paths,
            {
                "job_id": job_id,
                "status": "complete",
                "step": "done",
                "progress": 1.0,
                "message": "Ready",
                "artifacts": {
                    "preview": str(outputs["preview"]),
                    "final": str(outputs["final"]),
                    "edl": str(paths.edl_path),
                },
            },
        )
    except (FFmpegError, RuntimeError) as exc:
        _update_status(
            paths,
            {
                "job_id": job_id,
                "status": "error",
                "step": "failed",
                "progress": 1.0,
                "message": str(exc),
            },
        )
        raise
