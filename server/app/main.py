from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

import subprocess
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.pipeline.runner import ClipInput, run_job
from app.utils.paths import ROOT_DIR, ensure_job_dirs, get_job_paths
from app.utils.status import read_status, write_status

ALLOWED_CLIP_EXTENSIONS = {".mp4", ".mov", ".webm"}
ALLOWED_SONG_EXTENSIONS = {".mp3", ".m4a", ".wav"}

app = FastAPI(title="Code X Local API", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _safe_suffix(filename: str) -> str:
    return Path(filename).suffix.lower()


def _save_upload(upload: UploadFile, destination: Path) -> None:
    with destination.open("wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)


def _parse_settings(settings_raw: Optional[str]) -> dict[str, Any]:
    if not settings_raw:
        return {}
    try:
        payload = json.loads(settings_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid settings JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Settings must be a JSON object")
    return payload


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _git_info() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(ROOT_DIR), "rev-parse", "--short", "HEAD"],
            text=True,
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(ROOT_DIR), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
        ).strip()
        log_output = subprocess.check_output(
            [
                "git",
                "-C",
                str(ROOT_DIR),
                "log",
                "-n",
                "5",
                "--pretty=format:%h|%s|%cI",
            ],
            text=True,
        ).strip()
        recent = []
        if log_output:
            for line in log_output.splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3:
                    recent.append({"commit": parts[0], "message": parts[1], "date": parts[2]})
        return {"commit": commit, "branch": branch, "recent": recent}
    except subprocess.SubprocessError:
        return {"commit": "unknown", "branch": "unknown", "recent": []}


@app.get("/version")
async def version() -> dict[str, Any]:
    return {"version": "local-dev", "git": _git_info()}


@app.post("/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    clips: list[UploadFile] = File(...),
    song: UploadFile = File(...),
    settings: Optional[str] = Form(default=None),
) -> dict[str, str]:
    if len(clips) == 0:
        raise HTTPException(status_code=400, detail="At least one clip is required")
    if len(clips) > 20:
        raise HTTPException(status_code=400, detail="Maximum of 20 clips allowed")

    for clip in clips:
        if _safe_suffix(clip.filename) not in ALLOWED_CLIP_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported clip format: {clip.filename}")

    if _safe_suffix(song.filename) not in ALLOWED_SONG_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported song format: {song.filename}")

    settings_payload = _parse_settings(settings)

    job_id = uuid.uuid4().hex
    paths = ensure_job_dirs(job_id)

    clip_inputs: list[ClipInput] = []
    for index, clip in enumerate(clips, start=1):
        clip_id = f"c{index}"
        destination = paths.input_dir / f"{clip_id}{_safe_suffix(clip.filename)}"
        _save_upload(clip, destination)
        clip_inputs.append(ClipInput(clip_id=clip_id, path=destination, original_name=clip.filename))

    song_destination = paths.input_dir / f"song{_safe_suffix(song.filename)}"
    _save_upload(song, song_destination)

    job_payload = {
        "job_id": job_id,
        "settings": settings_payload,
        "clips": [
            {"clip_id": clip.clip_id, "filename": clip.original_name, "path": str(clip.path)}
            for clip in clip_inputs
        ],
        "song": {"filename": song.filename, "path": str(song_destination)},
    }
    paths.job_path.write_text(json.dumps(job_payload, indent=2))

    write_status(
        paths.status_path,
        {
            "job_id": job_id,
            "status": "queued",
            "step": "queued",
            "progress": 0.0,
            "message": "Queued",
        },
    )

    background_tasks.add_task(run_job, job_id, clip_inputs, song_destination, settings_payload)

    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    paths = get_job_paths(job_id)
    status = read_status(paths.status_path)
    if status is None:
        raise HTTPException(status_code=404, detail="Job not found")

    response: dict[str, Any] = {"job_id": job_id, **status}
    artifacts: dict[str, str] = {}
    preview_path = paths.output_dir / "preview.mp4"
    final_path = paths.output_dir / "final.mp4"
    edl_path = paths.edl_path

    if preview_path.exists():
        artifacts["preview"] = f"/jobs/{job_id}/preview.mp4"
    if final_path.exists():
        artifacts["final"] = f"/jobs/{job_id}/final.mp4"
    if edl_path.exists():
        artifacts["edl"] = f"/jobs/{job_id}/edl.json"

    if artifacts:
        response["artifact_urls"] = artifacts

    return response


@app.get("/jobs/{job_id}/preview.mp4")
async def get_preview(job_id: str) -> FileResponse:
    paths = get_job_paths(job_id)
    preview_path = paths.output_dir / "preview.mp4"
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="Preview not ready")
    return FileResponse(preview_path)


@app.get("/jobs/{job_id}/final.mp4")
async def get_final(job_id: str) -> FileResponse:
    paths = get_job_paths(job_id)
    final_path = paths.output_dir / "final.mp4"
    if not final_path.exists():
        raise HTTPException(status_code=404, detail="Final render not ready")
    return FileResponse(final_path)


@app.get("/jobs/{job_id}/edl.json")
async def get_edl(job_id: str) -> FileResponse:
    paths = get_job_paths(job_id)
    if not paths.edl_path.exists():
        raise HTTPException(status_code=404, detail="EDL not ready")
    return FileResponse(paths.edl_path)
