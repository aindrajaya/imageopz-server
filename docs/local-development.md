# Local development (Windows/macOS/Linux)

This backend runs as **two processes**:

- API server (FastAPI)
- Worker (Redis queue worker + cleanup scheduler)

## 1) Prereqs

- Python 3.10+ (recommended: 3.11/3.12)
- Redis running locally

## 2) Setup

From the repo root:

- Create venv:
  - Windows (PowerShell): `python -m venv .venv`
  - macOS/Linux: `python3 -m venv .venv`
- Activate venv:
  - Windows (PowerShell): `.venv\\Scripts\\Activate.ps1`
  - macOS/Linux: `source .venv/bin/activate`
- Install deps: `pip install -r requirements.txt`

## 3) Environment variables

- Copy: `.env.example` -> `.env`
- Minimum recommended for local:
  - `ENVIRONMENT=development`
  - `DEBUG=True`
  - `SECRET_KEY=dev-secret`
  - `REDIS_URL=redis://localhost:6379/0`

Notes:
- If you don’t set R2 credentials, uploads will still be processed, but `r2_url` will be empty.
- For LemonSqueezy webhook testing you must set `LEMONSQUEEZY_SECRET`.

## 4) Start Redis

- Linux/macOS: `redis-server`
- Windows: run Redis via WSL2, Docker, or a Windows Redis build.

## 5) Run API

From repo root (with venv active):

- `python run.py --reload`

API is available at:
- `http://127.0.0.1:8000/`
- Swagger UI: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

## 6) Run worker (separate terminal)

From repo root (with venv active):

- `python run_worker.py`

## 7) Quick manual test

- Submit an image:
  - `curl -F \"images=@./path/to/image.png\" http://127.0.0.1:8000/api/v1/optimize`
- Poll status (replace job id):
  - `curl http://127.0.0.1:8000/api/v1/status/<job_id>`

## 8) Generate OpenAPI

- `python scripts/generate_openapi.py`
- Output: `docs/openapi.json`

## 9) Run tests

- `pytest -q`

