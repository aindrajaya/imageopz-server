# Runbook: run the app locally

Read when you need a running API (tests need no running services).

- Install: `pip install -r requirements.txt`; copy `.env.example` to `.env`. Minimum local values: ENVIRONMENT=development, DEBUG=True, SECRET_KEY.
- Uploads: the only path is presigned direct-to-R2 (`/api/v1/uploads/presign` and `/complete`; contract `contracts/uploads-api.md`); there is no local upload directory.
  Presign needs the uploads bucket (`R2_UPLOADS_*`); the worker needs the outputs bucket (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`)
  and fails a job without it rather than marking it done.
- API (FastAPI/uvicorn, port 8000): `python run.py --reload`. The worker is the same app: `POST /internal/jobs/process` (ADR-005).
- Jobs locally (direct mode, no queue): `JOB_ENQUEUE_MODE=direct`, `JOB_TASKS_TARGET_URL=http://localhost:8000`,
  `INTERNAL_AUTH_MODE=shared_secret` and the same `INTERNAL_AUTH_SHARED_SECRET` for sender and receiver.
  `/uploads/complete` then posts each job to the endpoint and waits until it is processed. Without `JOB_ENQUEUE_MODE`,
  `/complete` answers 503 QUEUE_UNAVAILABLE (fail closed).
- Sweeper (expired uploads, stale PENDING re-enqueue, stuck PROCESSING): `python -m app.cli.sweep_uploads`.
- Expired-output cleanup: `python -m app.cli.cleanup_expired`. Both CLIs are idempotent. Deployed, Cloud Scheduler calls the
  same code on the worker: `POST /internal/maintenance/sweep` and `/cleanup` (internal auth; locally `X-Internal-Token`).
- `SERVICE_ROLE` (default `all`): `api` mounts public routes only, `worker` only `/internal/*` and health; anything else fails at start.
- Health: `/health` (liveness, no DB) and `/readyz` (DB check, 503 when down).
- OpenAPI spec: `IMAGEOPZ_ENV_FILE="" python scripts/generate_openapi.py` regenerates `docs/openapi.json`.
