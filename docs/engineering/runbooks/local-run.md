# Runbook: run the app locally

Read when you need a running API or worker (tests do not need Redis).

- Install: `pip install -r requirements.txt`; copy `.env.example` to `.env`. Minimum local values: ENVIRONMENT=development, DEBUG=True, SECRET_KEY, REDIS_URL.
- Redis is required for the job queue until Cloud Tasks replaces it: `redis-server`.
- Uploads: the only path is presigned direct-to-R2 (`/api/v1/uploads/presign` and `/complete`; contract `contracts/uploads-api.md`); there is no local upload directory.
  Presign needs the uploads bucket (`R2_UPLOADS_*`); the worker needs the outputs bucket (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`)
  and fails a job without it rather than marking it done.
- API (FastAPI/uvicorn, port 8000): `python run.py --reload`.
- Worker plus cleanup scheduler (separate process): `python run_worker.py`.
- OpenAPI spec: `python scripts/generate_openapi.py` regenerates `docs/openapi.json`.
- LEGACY: `python monitor_memory.py` watches API and worker memory (written for the old VPS; low value on Cloud Run; removal is T-007).
