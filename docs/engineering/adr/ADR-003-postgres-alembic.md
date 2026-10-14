# ADR-003: Postgres support with Alembic-owned schema

Status: proposed
Date: 2026-10-15
Context task: T-003

## Context
Target hosting is Cloud Run + Supabase Postgres. The schema was created at app startup by
`models.py:Database.init_db` (`create_all`, raw `ALTER TABLE`, a raw unique index). That is SQLite-specific, drifted from
the models (`pro_credits_used` / `pro_verified` NOT NULL via ALTER vs nullable in the model), and unsafe with several
instances starting at once. We start with an empty Postgres (no data migration).

## Decision
1. Alembic owns the schema on non-SQLite databases. `init_db` performs no DDL there and logs "Schema managed by Alembic".
   The SQLite legacy path is unchanged (`TODO(debt)`: remove once Postgres is the default for local dev).
2. Baseline revision `0001` creates the full schema; downgrade drops it.
3. Models declare what the legacy ALTERs enforced: `users_quota.pro_credits_used` NOT NULL DEFAULT 0,
   `pro_verified` NOT NULL DEFAULT false; `server_default` on `credits_used` and `is_pro`;
   `users.supabase_user_id` unique index `idx_users_supabase_user_id_unique` (same name as the old raw index).
4. `jobs.status` stays `VARCHAR(10)` + CHECK (`native_enum=False`, constraint `jobstatus`) on every backend.
   The stored value is the enum member NAME (`PENDING`, `PROCESSING`, `DONE`, `FAILED`), exactly as before;
   pinned by `tests/test_job_status_storage.py`. No native Postgres enum, so no `ALTER TYPE` in future changes.
5. **RLS deny-all.** Supabase's Data API exposes the `public` schema, so the baseline enables
   `ROW LEVEL SECURITY` on every app table (PostgreSQL only) with NO policies: `anon` / `authenticated` see nothing even if
   Supabase's default grants allow SELECT. The backend connects as the table owner, which bypasses RLS (do not use FORCE and do not
   connect as a role without BYPASSRLS/ownership). `alembic_version` is covered too. Each future revision that adds a table must enable RLS too; a test enforces this
   for every table in the models.
6. **Naming convention** on the declarative `MetaData` (`ix_`, `uq_`, `ck_`, `fk_`, `pk_`) so names are identical on SQLite and Postgres.
   Explicit names (`idx_users_supabase_user_id_unique`, `uq_orders_provider_external_id`, ...) are unchanged; the `jobstatus` CHECK keeps
   its name via `conv("jobstatus")` (otherwise the `ck_` convention would rename it `ck_jobs_jobstatus`).
7. `users.supabase_user_id` is indexed only by the unique index (no extra `index=True`).
8. Redundant single-column `Index(...)` entries that duplicated `index=True` columns were removed (see T-003 handoff).
9. Postgres engine: `pool_pre_ping`, `pool_size=DB_POOL_SIZE` (default 2), `max_overflow=0`, psycopg3
   `prepare_threshold=None` (no prepared statements, required by Supabase's transaction pooler).
   Bare `postgresql://` / `postgres://` URLs are mapped to `postgresql+psycopg://`.
10. Tests: `TEST_DATABASE_URL` selects Postgres (fresh DB, `alembic upgrade head`, dropped afterwards); default is temp SQLite.
   CI runs both legs.

## Consequences
- Deploys run `alembic upgrade head` (Cloud Run Job) before new revisions serve traffic.
- Existing SQLite files keep their old duplicate indexes (no automatic cleanup).
- Run-time migrations are no longer implicit on Postgres: forgetting to migrate fails at the first query.
- `alembic_version` (also in `public`) gets RLS deny-all in the baseline too: Alembic creates it before running revisions, and the owner role that runs migrations bypasses RLS.
- Out of scope / follow-ups: `datetime.utcnow` (naive timestamps), rate limiting, uploads/queue, data migration from SQLite.


## Reconciled 2026-10-15
- Implemented on `main` via PR #1 (`f5c1266`). Status is still "proposed" in this file; changing it to "accepted" is an owner decision.
- RLS on `alembic_version` (see decision 5) is implemented: `alembic/versions/0001_baseline.py:139-140`.
- ADR-001 (payments ingress) and ADR-002 (hosting) were added on `docs/engineering-state` (T-DOCS-1b), status proposed.
