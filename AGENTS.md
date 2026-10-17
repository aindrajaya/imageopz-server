# AGENTS.md

Shared agent memory (CLAUDE.md is a symlink to this file). Every rule carries a one-line why.

## Read first
- docs/engineering/STATE.md - objective, baseline, open tasks; a snapshot, overwritten each task
- docs/engineering/tasks/ (contracts, handoffs), adr/ (decisions), registers.md (lessons, debt, protected behaviors)

## Stack
- Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic; Postgres 16 (Supabase in prod), SQLite for quick local tests
- Cloudflare R2 (outputs), LemonSqueezy (payments), Supabase Auth (JWT via JWKS), Resend (email fallback)
- Target hosting Cloud Run (ADR-002); Go payments ingress planned (ADR-001); currently offline

## Layout
- app/routes/: optimization.py (optimize, status, checkout, LemonSqueezy webhook; large), uploads.py (presign, complete), session.py, health.py
- Uploads: browser PUTs to R2 via presigned URLs; contract docs/engineering/contracts/uploads-api.md; sweeper python -m app.cli.sweep_uploads
- app/models.py, app/config.py (env-driven settings and tier limits), app/utils/ (security, auth_deps, supabase_auth)
- app/workers/job_worker.py: worker behind POST /internal/jobs/process, fed by Cloud Tasks (ADR-005); app/services/job_enqueuer.py
- CLIs (scheduled in T-008): python -m app.cli.sweep_uploads (expired uploads, stale jobs), python -m app.cli.cleanup_expired
- app/services/: image_service.py (Pillow/AVIF, R2), email_service.py; alembic/; tests/; issues/ (incident notes)
- Identity: anonymous users by cookie imageopz_sid or X-Session-Id; Pro users by Supabase JWT; UsersQuota holds credits and Pro state
- docs/PRD-backend.v3.md is the latest spec; ci-convert.py is a standalone AVIF CLI, unrelated to the server

## Commands
- Tests on SQLite: pytest -q in the project venv; one test: pytest tests/test_x.py -k name
- Postgres leg: TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/postgres pytest -q (fresh DB built by Alembic)
- Migrations: alembic upgrade head; after model changes alembic revision --autogenerate, review, then alembic check
- No linter or formatter is configured

## Rules
- Schema changes only through Alembic, never create_all/ALTER in app code - single DDL owner (ADR-003)
- Every new table enables RLS on Postgres - Supabase Data API exposes public; tests/test_rls_deny_all.py enforces it
- App and migrations connect as the table-owner role - non-owners see zero rows under RLS
- Python is the only writer of users, users_quota, orders; activation is idempotent per order - avoids drift and double grants (ADR-001)
- Commit the Job row before enqueueing; the worker retries lookups - the queue can deliver before the DB commit is visible (issues/RACE_CONDITION_FIX.md)
- Tests reload app.config, app.models, app.main per test; follow tests/conftest.py - settings and db are module singletons
- Tests never load .env or reach external hosts; the conftest guard enforces it - a collection-time import once sent live API calls (T-006b)
- Never edit existing test assertions to make a change pass; behavior changes update tests deliberately and say so - tests are the contract
- Reproduce a bug with a failing test at the closest realistic level before fixing it - proves the fix
- Choose designs on simplicity, robustness and maintainability; ignore implementation time, weigh operating cost and solo-maintainer burden
- Fix lint/type/test problems your change introduces in the same change; log pre-existing defects as follow-ups in the task file - no silent scope creep
- Stay inside the task's scope and authorization; the owner commits and pushes; no git stash/reset/clean or history rewrite - keeps the owner's work safe
- Never read or print .env files, local DB files or secrets; never put secrets in code, logs or docs - they leak through history
- New dependencies need a stated reason and an exact pin - reproducible builds
- Never run install, dev, build or test commands on commits or branches you have not inspected; never on UI commits before fd1032e - build tooling executes repository code
- Plain hyphens, no em-dashes; no AI co-author trailers; never hand-edit generated files (Alembic revisions after review) - owner convention

## Learned (date - rule - why)
- 2026-10-15 - STATE.md is a snapshot: overwrite it each task; append only to task files - keeps STATE short and current
- 2026-10-15 - Squash merges: reference the squash SHA and PR number, not branch commits - branch SHAs vanish from main
- 2026-10-15 - Merge only after the PR's own checks pass (`gh pr checks <n> --watch && gh pr merge`) - `gh run watch` can select another branch's run.
- 2026-10-15 - Verify ruleset/required-check names against real check-run names - a placeholder blocks every PR.
- 2026-10-15 - Never assert the order of items processed in sorted-id order; UUIDs are random, so the test is flaky in CI.
- 2026-10-17 - Run Alembic only with `IMAGEOPZ_ENV_FILE=""` and `-x url=<throwaway db>` - without them env.py reads settings, which load .env and its database.
- 2026-10-17 - Never run two agent sessions in one working tree; use git worktree - concurrent sessions overwrite each other's edits.

## Runbooks (docs/engineering/runbooks/)
- local-run.md - start the API and worker locally, regenerate the OpenAPI spec
- local-postgres.md - throwaway Postgres cluster for the Postgres test leg
- db-migrations.md - writing and reviewing migrations, Supabase pooler modes, RLS for new tables
