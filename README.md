# Code X — Local VHS Highlight Reel Editor (MVP)

Local-first prototype for auto-editing night-out clips into a short vertical reel with a VHS look.

## Stack
- Frontend: React + Vite
- Backend: FastAPI (Python)
- Processing: FFmpeg (local install)

## Quickstart

### 1) Install FFmpeg
Make sure `ffmpeg` and `ffprobe` are available in your shell.

### 2) Start the API
```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3) Start the UI
```bash
cd apps/web
npm install
npm run dev
```

The UI expects the API at `http://localhost:8000`. Override with:
```bash
VITE_API_BASE=http://localhost:8000 npm run dev
```

## Notes
- Rendering is a minimal Phase 0/1 pipeline: proxies + naive segmenting + VHS filter + music track.
- Outputs are stored in `server/jobs/{job_id}/output`.
