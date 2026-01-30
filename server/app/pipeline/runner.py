from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from app.ai.fastvlm import tag_frame
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


def _extract_frames(proxy: ProxyClip, frames_dir: Path) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / f"{proxy.clip_id}_%04d.jpg"
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(proxy.path),
        "-vf",
        "fps=1,scale=384:-1:flags=lanczos",
        "-q:v",
        "2",
        str(pattern),
    ]
    run_ffmpeg(args)
    return sorted(frames_dir.glob(f"{proxy.clip_id}_*.jpg"))


def analyze_clips(
    paths: JobPaths,
    proxies: list[ProxyClip],
    settings: dict[str, Any],
    status_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, list[dict[str, Any]]]:
    labels: dict[str, list[dict[str, Any]]] = {}
    total_est = sum(max(1, int(proxy.duration)) for proxy in proxies) or 1
    processed = 0
    for proxy in proxies:
        clip_dir = paths.frames_dir / proxy.clip_id
        frame_paths = _extract_frames(proxy, clip_dir)
        clip_labels: list[dict[str, Any]] = []
        for frame_path in frame_paths:
            try:
                index = int(frame_path.stem.split("_")[-1]) - 1
            except ValueError:
                index = len(clip_labels)
            label = tag_frame(frame_path)
            label["timestamp"] = float(max(0, index))
            clip_labels.append(label)
            processed += 1
            if status_callback and (processed % 2 == 0 or label.get("scene")):
                progress = 0.35 + 0.15 * min(1.0, processed / total_est)
                caption = label.get("scene") or "no caption"
                status_callback(
                    {
                        "step": "analyze",
                        "progress": round(progress, 3),
                        "message": (
                            f"Tagging {proxy.clip_id} @ {label['timestamp']:.1f}s "
                            f"({processed}/{total_est}) • {caption}"
                        ),
                    }
                )
        labels[proxy.clip_id] = clip_labels

    labels_path = paths.job_dir / "vlm_labels.json"
    labels_path.write_text(json.dumps(labels, indent=2))
    return labels


def _candidate_params(vibe: str) -> tuple[float, float]:
    vibe = (vibe or "").lower()
    if vibe == "chill":
        return (1.4, 0.7)
    if vibe == "chaotic":
        return (0.6, 0.4)
    return (0.9, 0.5)


def _aggregate_labels(
    labels: list[dict[str, Any]],
    start: float,
    end: float,
) -> dict[str, Any]:
    segment = [label for label in labels if start <= label["timestamp"] < end]
    if not segment and labels:
        segment = [min(labels, key=lambda item: abs(item["timestamp"] - start))]
    if not segment:
        return {
            "highlight": 4,
            "energy": 4,
            "people": 0,
            "brightness": 0.5,
            "shot_type": "other",
        }

    highlight = sum(item.get("highlight", 4) for item in segment) / len(segment)
    energy = sum(item.get("energy", 4) for item in segment) / len(segment)
    brightness = sum(item.get("brightness", 0.5) for item in segment) / len(segment)
    people = max(item.get("people", 0) for item in segment)
    shot_counts: dict[str, int] = {}
    for item in segment:
        shot = item.get("shot_type", "other")
        shot_counts[shot] = shot_counts.get(shot, 0) + 1
    shot_type = max(shot_counts, key=shot_counts.get) if shot_counts else "other"
    return {
        "highlight": highlight,
        "energy": energy,
        "people": people,
        "brightness": brightness,
        "shot_type": shot_type,
    }


def _score_candidate(features: dict[str, Any]) -> float:
    highlight = float(features.get("highlight", 4.0)) / 10.0
    energy = float(features.get("energy", 4.0)) / 10.0
    brightness = float(features.get("brightness", 0.5))
    people = float(features.get("people", 0.0))

    quality = 1.0
    if brightness < 0.2:
        quality -= 0.25
    if brightness < 0.1:
        quality -= 0.2

    people_bonus = 0.1 if people >= 2 else 0.0
    score = 0.55 * highlight + 0.25 * energy + 0.2 * quality + people_bonus
    return max(0.0, min(1.0, score))


def _overlaps(existing: list[dict[str, Any]], start: float, end: float) -> bool:
    for item in existing:
        if start < item["out"] - 0.05 and end > item["in"] + 0.05:
            return True
    return False


def _build_vlm_timeline(
    proxies: list[ProxyClip],
    labels: dict[str, list[dict[str, Any]]],
    settings: dict[str, Any],
    rng: random.Random,
) -> list[dict[str, Any]]:
    target_length = float(settings.get("target_length_s", 15))
    vibe = settings.get("vibe", "hype")
    seg_len, stride = _candidate_params(vibe)

    candidates: list[dict[str, Any]] = []
    for proxy in proxies:
        clip_labels = labels.get(proxy.clip_id, [])
        start = 0.0
        while start + seg_len <= proxy.duration + 0.01:
            end = min(proxy.duration, start + seg_len)
            features = _aggregate_labels(clip_labels, start, end)
            score = _score_candidate(features)
            candidates.append(
                {
                    "clip_id": proxy.clip_id,
                    "in": round(start, 3),
                    "out": round(end, 3),
                    "score": score,
                    "shot_type": features.get("shot_type", "other"),
                    "people": features.get("people", 0),
                }
            )
            start += stride

    candidates.sort(key=lambda item: item["score"], reverse=True)
    if not candidates:
        return []

    max_per_clip = target_length * 0.25
    selected: list[dict[str, Any]] = []
    clip_usage: dict[str, float] = {}
    clip_segments: dict[str, list[dict[str, Any]]] = {}

    hook_pool = candidates[: max(1, len(candidates) // 10)]
    hook = next((item for item in hook_pool if item.get("people", 0) >= 1), hook_pool[0])
    selected.append(hook)
    clip_usage[hook["clip_id"]] = hook["out"] - hook["in"]
    clip_segments[hook["clip_id"]] = [hook]

    shot_history = [hook.get("shot_type", "other")]
    total = clip_usage[hook["clip_id"]]

    for candidate in candidates:
        if total >= target_length - 0.05:
            break
        if candidate is hook:
            continue
        clip_id = candidate["clip_id"]
        seg_len_candidate = candidate["out"] - candidate["in"]
        remaining = target_length - total
        if remaining < 0.5:
            break
        if seg_len_candidate > remaining:
            candidate = {**candidate, "out": round(candidate["in"] + remaining, 3)}
            seg_len_candidate = candidate["out"] - candidate["in"]

        if clip_usage.get(clip_id, 0.0) + seg_len_candidate > max_per_clip:
            continue
        if _overlaps(clip_segments.get(clip_id, []), candidate["in"], candidate["out"]):
            continue
        if len(shot_history) >= 2 and shot_history[-1] == shot_history[-2] == candidate.get(
            "shot_type"
        ):
            continue

        selected.append(candidate)
        clip_usage[clip_id] = clip_usage.get(clip_id, 0.0) + seg_len_candidate
        clip_segments.setdefault(clip_id, []).append(candidate)
        shot_history.append(candidate.get("shot_type", "other"))
        total += seg_len_candidate

    if total < target_length - 0.25:
        for candidate in candidates:
            if total >= target_length - 0.05:
                break
            if candidate in selected:
                continue
            clip_id = candidate["clip_id"]
            if _overlaps(clip_segments.get(clip_id, []), candidate["in"], candidate["out"]):
                continue
            remaining = target_length - total
            seg_len_candidate = candidate["out"] - candidate["in"]
            if remaining < 0.5:
                break
            if seg_len_candidate > remaining:
                candidate = {**candidate, "out": round(candidate["in"] + remaining, 3)}
                seg_len_candidate = candidate["out"] - candidate["in"]
            selected.append(candidate)
            clip_usage[clip_id] = clip_usage.get(clip_id, 0.0) + seg_len_candidate
            clip_segments.setdefault(clip_id, []).append(candidate)
            total += seg_len_candidate

    if len(selected) > 1:
        rest = selected[1:]
        rng.shuffle(rest)
        selected = [selected[0], *rest]

    return selected


def _build_random_timeline(
    proxies: list[ProxyClip],
    settings: dict[str, Any],
    rng: random.Random,
) -> list[dict[str, Any]]:
    target_length = float(settings.get("target_length_s", 15))
    vibe = settings.get("vibe", "hype")
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
    return timeline


def build_edl(
    paths: JobPaths,
    proxies: list[ProxyClip],
    settings: dict[str, Any],
    labels: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> dict[str, Any]:
    target_length = float(settings.get("target_length_s", 15))
    vibe = settings.get("vibe", "hype")
    seed = _resolve_seed(settings)
    rng = random.Random(seed)

    timeline: list[dict[str, Any]] = []
    if labels:
        timeline = _build_vlm_timeline(proxies, labels, settings, rng)
    if not timeline:
        timeline = _build_random_timeline(proxies, settings, rng)

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
                "step": "analyze",
                "progress": 0.35,
                "message": "Tagging frames (FastVLM)",
            },
        )

        labels: Optional[dict[str, list[dict[str, Any]]]] = None
        try:
            def _status_update(update: dict[str, Any]) -> None:
                _update_status(
                    paths,
                    {
                        "job_id": job_id,
                        "status": "running",
                        **update,
                    },
                )

            labels = analyze_clips(paths, proxies, settings, _status_update)
        except Exception as exc:
            _update_status(
                paths,
                {
                    "job_id": job_id,
                    "status": "running",
                    "step": "analyze",
                    "progress": 0.4,
                    "message": f"Tagging failed, falling back: {exc}",
                },
            )
            labels = None

        _update_status(
            paths,
            {
                "job_id": job_id,
                "status": "running",
                "step": "edl",
                "progress": 0.5,
                "message": "Building edit decision list",
            },
        )

        edl = build_edl(paths, proxies, settings, labels)

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
