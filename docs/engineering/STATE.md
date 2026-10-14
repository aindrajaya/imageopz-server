# STATE — ImageOpz payments migration

## Baseline
- T-000 baseline: main@bee0a98.
- T-001 working branch: test/webhook-characterization @ bee0a98 + uncommitted changes (not committed).
- Backend: FastAPI + SQLAlchemy + SQLite (WAL), systemd + Nginx, no Docker.
- Test env: Python 3.11.2 venv at `~/.venvs/imageopz` (outside repo); run `~/.venvs/imageopz/bin/pytest -q`. Suite: 27 passed (15 baseline + 12 characterization).
- CI: `.github/workflows/backend-tests.yml` (Python 3.11), added in T-001, not yet run on GitHub.

## Payment boundary (see tasks/T-000-recon.md)
- Webhook: POST /api/v1/webhooks/lemonsqueezy -> app/routes/optimization.py:handle_lemonsqueezy_webhook
- Signature: X-Signature, HMAC-SHA256 hex of raw body, compare_digest (app/utils/security.py:WebhookValidator)
- Writes: orders, users, users_quota, checkout_intents, jobs.expiry_date
- Other writers of users_quota Pro fields: app/routes/session.py, app/routes/optimization.py
- Known gaps (now pinned by tests/test_webhook_characterization.py): replay re-activates, refunds ignored, non-atomic commits, 500 echoes exception text, ip_hash-only Pro path.

## Tasks
- T-000 recon: done (tasks/T-000-recon.md)
- T-001 characterization tests + CI: done, awaiting review/commit (tasks/T-001-characterization.md)

## Decisions pending
- Column-level write ownership between Go and FastAPI
- Replay / refund semantics; fate of ip_hash path
- Who owns DDL / migrations

## Next step
- Review and commit T-001; then T-002 (fix agreed behaviors test-first, edit the "currently" tests deliberately).

## Update 2026-10-15 — T-003 Postgres + Alembic (uncommitted)
- Branch: feat/postgres-alembic from 3e6c6d8. Added alembic/ (baseline 0001), alembic.ini, pinned `alembic==1.16.5`, `psycopg[binary]==3.2.13`.
- Postgres: no DDL at app startup (init_db logs "Schema managed by Alembic"); SQLite legacy path kept (TODO(debt)). Pool: DB_POOL_SIZE (default 2), pre-ping, prepared statements off.
- Models: pro_credits_used/pro_verified NOT NULL + server defaults; unique index on users.supabase_user_id; 10 duplicate single-column indexes removed; jobs.status VARCHAR+CHECK storing member NAMES (pinned by tests/test_job_status_storage.py).
- Tests: SQLite 32 passed/1 skipped; Postgres (TEST_DATABASE_URL) 33 passed. CI matrix sqlite+postgres added (not yet run on GitHub).
- Details: tasks/T-003-postgres-alembic.md, adr/ADR-003-postgres-alembic.md (proposed).
- Open: Supabase pooler/TLS verification, utcnow cleanup, rate limiting, uploads/queue, Dockerfile, ownership of Go vs FastAPI writes.
- Next step: review/commit T-003; verify against a Supabase staging DB; T-004 Dockerfile + Cloud Run migration Job.

### T-003b polish (2026-10-15, uncommitted)
- Baseline 0001 edited in place: RLS deny-all on all 5 app tables (PG only) + Postgres-only test; naming convention on Base.metadata (jobstatus CHECK pinned via conv); `users.supabase_user_id` no longer double-indexed.
- Tests: SQLite 32 passed/3 skipped; Postgres 35 passed. `alembic check` clean. AC7: 3 cosmetic inspector diffs.
- Note: app must connect as table owner (RLS bypass).
- T-003b.1: RLS deny-all also on `alembic_version` (baseline + test). Postgres suite 36 passed; SQLite 32 passed/4 skipped; downgrade base -> upgrade head OK.
