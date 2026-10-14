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

## Database & migrations
- Local quick runs use SQLite (`DATABASE_URL=sqlite:///./imageopz.db`); the app still creates/alters tables itself on that path (legacy, to be removed).
- For Postgres (e.g. Supabase) the schema is owned by Alembic: the app performs **no DDL** at startup. Use `postgresql://...` or `postgresql+psycopg://...` URLs (the psycopg3 driver is used; `sslmode` is read from the URL). Pool size: `DB_POOL_SIZE` (default 2, no overflow). Prepared statements are disabled so the Supabase transaction pooler works.
- Apply migrations: `alembic upgrade head`
- Roll back everything: `alembic downgrade base`
- New revision after changing `app/models.py`: `alembic revision --autogenerate -m "describe change"`, review the file, then `alembic check` must report no diff.
- Tests against Postgres: `TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/postgres pytest` (a fresh database is created, migrated with Alembic, and dropped afterwards). Without it, tests use a temp SQLite file.
- Decision record: `docs/engineering/adr/ADR-003-postgres-alembic.md`

## Production
- Use systemd and run API + worker as separate services: `docs/production-runbook.md`

## OpenAPI spec
- Generate: `python scripts/generate_openapi.py`
- Output: `docs/openapi.json`
