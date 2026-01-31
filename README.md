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

### VHS look (ntsc-rs)
This build uses `ntsc-rs` for the VHS pass. Install the app and ensure the CLI is reachable:

```bash
# Option A: add to PATH (macOS)
ln -s /Applications/ntsc-rs.app/Contents/MacOS/ntsc-rs-cli /usr/local/bin/ntsc-rs-cli

# Option B: set an explicit path
export NTSC_RS_CLI_PATH=/Applications/ntsc-rs.app/Contents/MacOS/ntsc-rs-cli
```

Presets live in `server/presets/ntsc`. The default preset is `ntsc_custom.json` (based on the provided settings), and you can override by setting `ntsc_preset` in job settings (e.g., `semi-sharp`, `game-tape`, or `dynamic` for per-segment variation).

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
- Rendering is a minimal Phase 0/1 pipeline: proxies + beat sync + AI/heuristic segmenting + VHS filter + music track.
- Default output resolution is `1360x1824` to match the input aspect ratio (override via `settings.resolution`).
- Outputs are stored in `server/jobs/{job_id}/output`.

## FastVLM tagging (AI-assisted selection)
The pipeline now samples 1 fps frames and runs a lightweight image-to-text model to score highlights.
If you want a different model, set `FASTVLM_MODEL` before starting the API.
