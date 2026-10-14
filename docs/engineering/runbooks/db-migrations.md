# Runbook: database migrations

Read before changing `app/models.py` or touching `alembic/`.

- Create: edit models, then `alembic revision --autogenerate -m "describe change"`. Review the file by hand; never trust autogenerate blindly.
- Verify: `alembic upgrade head`, `alembic check` (must report no diff), `alembic downgrade base` then `upgrade head` on an empty database.
- Naming: the declarative MetaData has a naming convention; explicit names stay as written. The `jobstatus` CHECK is pinned with `conv("jobstatus")` / `op.f('jobstatus')`.
- New tables: enable RLS in the same revision (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`, Postgres only, no policies). tests/test_rls_deny_all.py fails if a table is missed.
- Roles: the app and the migration job connect as the table-owner role; a non-owner without BYPASSRLS sees zero rows.
- Supabase: migrations use the session pooler (port 5432); services use the transaction pooler (port 6543), which is why prepared statements are disabled (`prepare_threshold=None`).
- `jobs.status` is stored as the enum member NAME (`PENDING`, ...); do not change the stored representation (tests/test_job_status_storage.py).
- SQLite legacy DDL in `init_db` is debt; do not extend it.
