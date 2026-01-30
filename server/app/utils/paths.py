from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
JOBS_DIR = ROOT_DIR / "jobs"


@dataclass(frozen=True)
class JobPaths:
    job_dir: Path
    input_dir: Path
    proxy_dir: Path
    frames_dir: Path
    output_dir: Path
    edl_path: Path
    status_path: Path
    job_path: Path


def get_job_paths(job_id: str) -> JobPaths:
    job_dir = JOBS_DIR / job_id
    return JobPaths(
        job_dir=job_dir,
        input_dir=job_dir / "input",
        proxy_dir=job_dir / "proxy",
        frames_dir=job_dir / "frames",
        output_dir=job_dir / "output",
        edl_path=job_dir / "edl.json",
        status_path=job_dir / "status.json",
        job_path=job_dir / "job.json",
    )


def ensure_job_dirs(job_id: str) -> JobPaths:
    paths = get_job_paths(job_id)
    for path in [
        paths.job_dir,
        paths.input_dir,
        paths.proxy_dir,
        paths.frames_dir,
        paths.output_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)
    return paths
