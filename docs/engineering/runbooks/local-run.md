# Runbook: run the app locally

Read when you need a running API or worker (tests do not need Redis).

- Install: `pip install -r requirements.txt`; copy `.env.example` to `.env`. Minimum local values: ENVIRONMENT=development, DEBUG=True, SECRET_KEY, REDIS_URL.
- Redis is required for the job queue until Cloud Tasks replaces it: `redis-server`.
- API (FastAPI/uvicorn, port 8000): `python run.py --reload`.
- Worker plus cleanup scheduler (separate process): `python run_worker.py`.
- OpenAPI spec: `python scripts/generate_openapi.py` regenerates `docs/openapi.json`.
- LEGACY: `python monitor_memory.py` watches API and worker memory (written for the old VPS; low value on Cloud Run; removal is T-007).
