# T-003 — Postgres support via Alembic

Branch: feat/postgres-alembic (from 3e6c6d8). Not committed.
ADR: docs/engineering/adr/ADR-003-postgres-alembic.md (proposed).

## Handoff

**Baseline -> final:** 3e6c6d8 (clean apart from untracked CLAUDE.md) -> 3e6c6d8 + uncommitted working tree.
Tests: 27 passed at the start; +5 new tests for jobs.status storage written first and green on the unchanged code (32 passed);
final: SQLite 32 passed / 1 skipped, Postgres 33 passed. No existing assertion edited.

**Environment**
- Local Postgres: throwaway cluster from `/Applications/Postgres.app/Contents/Versions/latest/bin` (PostgreSQL 16.1, aarch64-apple-darwin),
  `initdb` in a mktemp dir, port 5433, trust auth on localhost, stopped and the directory deleted afterwards. Postgres.app's default cluster untouched.
- Deps added to requirements.txt (pinned): `alembic==1.16.5`, `psycopg[binary]==3.2.13` (installed into ~/.venvs/imageopz, Python 3.11.2).

**Evidence** (revision = 3e6c6d8 + uncommitted)

| Criterion | Command | Result | Excerpt |
|---|---|---|---|
| AC1 upgrade on empty PG | `DATABASE_URL=postgresql://postgres:dev@localhost:5433/imageopz_dev alembic upgrade head` | pass | `Running upgrade  -> 0001, baseline` |
| AC1 no autogenerate diff | `alembic check` | pass | `No new upgrade operations detected.` |
| AC2 reversible | `alembic downgrade base` then `upgrade head` | pass | after downgrade only `alembic_version` remained, no enum types; upgrade re-ran cleanly, `alembic check` clean |
| AC3 SQLite | `pytest -q` | pass | `32 passed, 1 skipped` |
| AC3 Postgres | `TEST_DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5433/postgres pytest -q` | pass | `33 passed`; fresh DB per session, schema via Alembic, 0 leftover `imageopz_test_*` databases |
| AC4 no DDL at startup on PG | `tests/test_db_schema_management.py` (Postgres only) | pass | asserts log "Schema managed by Alembic" and an identical column/index snapshot before/after `init_db()` |
| AC5 no business-logic change | `git diff --stat -- app/routes` | empty | only app/models.py and app/config.py changed under app/ |
| AC6 stored representation | `tests/test_job_status_storage.py` (raw SELECT) | pass on SQLite and PG | stores member NAME: `PENDING`/`PROCESSING`/`DONE`/`FAILED`; passed before and after the enum change |
| AC7 schema parity | scratch script comparing SQLite `create_all` vs PG baseline via the SQLAlchemy inspector | 4 cosmetic diffs only | see below |

**Diff size:** tracked +130 / -37, plus new files (env.py, ini, mako, 3 test files, ADR); generated migration `0001_baseline.py` is 175 lines. Under the ~400-line limit.

**Representation the code writes today (finding):** enum member names (uppercase), not the lowercase values. `native_enum=False` keeps that, so `values_callable` was not needed. The CHECK constraint `jobstatus` exists on both backends. No native Postgres enum exists, so the "create the jobstatus enum explicitly" step was unnecessary by decision.

**Schema differences found vs archived prod `.schema` (read-only, no rows)**
- `users_quota.pro_credits_used`, `pro_verified`, `pro_source` exist but are nullable in the archive (created by `create_all`, the ALTER never ran). Models now say NOT NULL DEFAULT 0 / false per the amendment; fine for an empty Postgres, but restoring archive rows with NULLs there would fail the constraint.
- `jobs` in the archive lacks `original_width`, `original_height`, `processing_started_at/finished_at`, `processing_duration_ms` (archive predates 8524930).
- Archive has duplicate single-column indexes (`ix_*` and `idx_*`) and `jobs.status VARCHAR(10)` without a CHECK.
- Archive has the raw unique index `idx_users_supabase_user_id_unique`; the model now declares it under the same name.

**Removed indexes (single-column duplicates of `index=True` columns), 10:** `idx_user_email`, `idx_users_supabase_user_id`, `idx_ip_hash`, `idx_session_id`, `idx_user_id`, `idx_orders_user_id`, `idx_orders_email`, `idx_status`, `idx_expiry_date`, `idx_user_quota_id`. Composite and unique indexes kept (`idx_orders_provider_external_id`, the three `idx_checkout_intents_*`).

**AC7 remaining differences (all inspector artifacts, not schema):**
1. PG also lists unique constraints `uq_checkout_intents_provider_checkout_id` and `uq_orders_provider_external_id` as unique indexes.
2. The unnamed UNIQUE on `jobs.r2_object_key` is auto-named `jobs_r2_object_key_key` on PG, unnamed on SQLite.
Columns, nullability, defaults, lengths, FKs, PKs, and CHECK names matched.

**Observations / follow-ups**
- `users.supabase_user_id` still has `index=True` plus the new unique index, so the non-unique `ix_users_supabase_user_id` is redundant (kept; rule 4 only removed exact duplicates of `index=True`).
- Existing SQLite DBs keep their old duplicate indexes (create_all does not drop).
- Name the `jobs.r2_object_key` unique constraint in a later revision for cross-backend parity.
- Follow-ups still open: `datetime.utcnow` cleanup (naive timestamps), rate limiting (Nginx limits lost on Cloud Run), uploads/queue/Redis, Docker image, data migration.

**Not run, and why**
- No Supabase/remote DB (out of scope; so pooler behavior `prepare_threshold=None` is unverified against a real transaction pooler). The psycopg3 option is set per psycopg's documented `prepare_threshold=None`, but I did not test it through PgBouncer or Supabase.
- `sslmode` from the URL is untested against a TLS server.
- The new CI Postgres matrix has not run on GitHub; the YAML parses locally.
- Downgrade of data-bearing databases was not exercised (empty DB only).

**Proposed next step:** review and commit T-003; set up a Supabase staging project to run `alembic upgrade head` through the pooler, then T-004 (Dockerfile + Cloud Run migration Job).


## T-003b — pre-commit polish (baseline edited in place; not applied anywhere)

Revision: 3e6c6d8 + uncommitted. Same throwaway Postgres.app cluster procedure (16.1, port 5433, stopped and deleted afterwards).

Changes
1. **RLS (must):** baseline enables `ROW LEVEL SECURITY` on `checkout_intents`, `jobs`, `orders`, `users`, `users_quota`, PostgreSQL only
   (`op.get_bind().dialect.name`), no policies. `tests/test_rls_deny_all.py` (Postgres-only, `skipif`): every metadata table has `relrowsecurity` and
   `pg_policies` is empty; with `anon` granted SELECT, `SET ROLE anon` returns 0 rows while the owner sees the inserted row. The test also
   disables RLS to prove `anon` would see the row otherwise, then re-enables it.
2. **Naming convention:** set on `Base.metadata` as specified. Explicit names unchanged. The `jobstatus` CHECK would have become
   `ck_jobs_jobstatus` under the `ck_` convention, so it is pinned with `conv("jobstatus")` in the model and `op.f('jobstatus')` in the migration
   (verified in `pg_constraint`: `jobstatus`). The unnamed unique on `jobs.r2_object_key` is now `uq_jobs_r2_object_key` on both backends.
   Baseline regenerated.
3. **Dropped `index=True`** on `users.supabase_user_id`; only `idx_users_supabase_user_id_unique` indexes it.

Re-run (AC1-AC7)

| Criterion | Command | Result |
|---|---|---|
| AC1 | `alembic upgrade head` on empty PG; `alembic check` | pass; `No new upgrade operations detected.` |
| AC2 | `alembic downgrade base` then `upgrade head`, `alembic check` | pass |
| AC3 SQLite | `pytest -q -rs` | `32 passed, 3 skipped` (the 3 are Postgres-only) |
| AC3 Postgres | `TEST_DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5433/postgres pytest -q` | `35 passed` |
| AC4 | `tests/test_db_schema_management.py` | pass on PG; uses module-level `pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, ...)`, confirmed |
| AC5 | `git diff --stat -- app/routes` | empty |
| AC6 | `tests/test_job_status_storage.py` | pass on SQLite and PG (stored value is still the member name) |
| AC7 | scratch inspector comparison, SQLite `create_all` vs PG baseline | 3 diffs, all PG listing unique constraints as unique indexes too; the previous `jobs_r2_object_key_key` naming diff is gone |

Catalog check on PG: `relrowsecurity` true for all 5 tables, false for `alembic_version`; 0 leftover `imageopz_test_*` databases.

Notes
- The application connects as the table owner (bypasses RLS). If a non-owner role without BYPASSRLS is ever used, every query returns zero rows.
- `alembic_version` RLS: see T-003b.1 below.
- Every future table-creating revision must enable RLS; the metadata-driven test will fail if one is missed.
- Diff vs HEAD (tracked): 7 files, +153/-39, plus new files; `app/routes` untouched.

## T-003b.1 — RLS on alembic_version

- Baseline `upgrade()` (PostgreSQL only) now also runs `ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY`. Confirmed the table exists at that point
  (Alembic creates it before running revisions; the upgrade succeeded and the catalog shows it).
- `tests/test_rls_deny_all.py::test_alembic_version_is_rls_protected_from_anon`: with SELECT/UPDATE/DELETE granted to `anon`, anon SELECT returns 0 rows
  and UPDATE/DELETE affect 0 rows, while the owner sees the revision row. Sanity leg: with RLS disabled the same statements hit the row
  (all probes are rolled back; the row count is verified unchanged at the end).
- Re-run (Postgres.app 16.1 throwaway cluster, port 5433, stopped and deleted afterwards):
  - `alembic upgrade head`, `alembic check` -> `No new upgrade operations detected.`; `relrowsecurity` true for all 6 `public` tables.
  - `alembic downgrade base && alembic upgrade head` -> pass; `alembic_version` kept RLS and holds `0001`; `alembic check` clean.
  - Postgres suite (`TEST_DATABASE_URL=...`): `36 passed`. SQLite suite: `32 passed, 4 skipped` (the 4 are Postgres-only).
- Not committed.
