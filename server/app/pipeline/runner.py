from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.utils.ffmpeg import FFmpegError, ffprobe_duration, run_ffmpeg
from app.utils.paths import JobPaths, ensure_job_dirs
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
    "resolution": "1080x1920",
    "fps": 30,
    "seed": None,
    "include_clip_audio": False,
    "locked_clips": [],
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
    filter_parts.append(
        f"{concat_inputs}concat=n={len(timeline)}:v=1:a=0[vcat]"
    )

    vhs = _vhs_filter(settings.get("vhs_intensity", 0.7))
    filter_parts.append(
        f"[vcat]fps=30,scale=1080:1920:flags=lanczos,{vhs},format=yuv420p[vout]"
    )

    target_length = float(edl["settings"]["target_length_s"])
    audio_index = len(timeline)
    filter_parts.append(
        f"[{audio_index}:a]atrim=0:{target_length},asetpts=PTS-STARTPTS[aout]"
    )

    filter_complex = ";".join(filter_parts)

    final_path = paths.output_dir / "final.mp4"
    args = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
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
        str(final_path),
    ]
    run_ffmpeg(args)

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
