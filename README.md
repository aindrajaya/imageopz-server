# ImageOpz Server

FastAPI backend for ImageOpz (bulk image optimization + async worker + R2 storage).

## Run (development)
- Install deps: `pip install -r requirements.txt`
- Start Redis locally (required for queue): `redis-server`
- Start API: `python run.py --reload`
- Start worker (+ cleanup scheduler): `python run_worker.py`

## Key endpoints (v1)
- `POST /api/v1/optimize` (multipart `images[]`, optional `auth_token`)
- `GET /api/v1/status/{job_id}`
- `POST /api/v1/webhooks/lemonsqueezy`
- `GET /health`

## Notes
- Uploaded files are streamed to `./tmp/uploads` and removed after processing.
- SQLite runs in WAL mode.
- `GET /api/v1/status/{job_id}` now includes original dimensions (`original_width`/`original_height`) and processing timing (`processing_*`) when available.

## Production
- Use systemd and run API + worker as separate services: `docs/production-runbook.md`

## OpenAPI spec
- Generate: `python scripts/generate_openapi.py`
- Output: `docs/openapi.json`
