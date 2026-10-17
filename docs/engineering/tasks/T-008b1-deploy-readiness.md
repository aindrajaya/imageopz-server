# T-008b1 - Deploy-readiness app changes: route roles, maintenance endpoints, H1, C1, header-only sessions
Branch `feat/deploy-readiness` from main@acd7b82 (T-008a, PR #18). Change type: feature + fixes. Rigor: Elevated (auth
surface). Executor: Claude Code. Local commits only; the owner pushes and merges. imageopz-ui read at main@f6f19ce
(read-only, nothing run).

## Contract (summary)
Records Q1-Q11; reconcile; SERVICE_ROLE api|worker|all (fail closed); POST /internal/maintenance/{sweep,cleanup} on
the worker with internal auth; H1 (/health generic, /readyz 503); C1 (Secure outside development); Q2-B header-only
anonymous flow; DEPLOY-PLAN, ADR-005 amendment, registers; independent review. AC: both suites green; /internal/*
unreachable on api; maintenance rejects unauthenticated calls; /health has no error text; header-only flow passes;
alembic check clean.

## Reconcile
HEAD acd7b82 = STATE baseline. SQLite 130 passed / 13 skipped, Postgres 16.15 (Homebrew) 143 passed: match STATE.

## Handoff
Commits (branch, local): bb4a5e3 feature, bec5f20 review fixes, plus the docs commit recorded in STATE.md.

Code
- `app/config.py`: `service_role: Literal["api","worker","all"] = "all"` (trimmed and lowercased; `""`, `bogus`,
  `api,worker` fail validation, so the process does not start); `session_cookie_secure()` = environment != development.
- `app/main.py`: `api` mounts health, optimization (incl. the public LemonSqueezy fallback webhook), uploads, session, `/`
  and CORS; `worker` mounts health and the internal router only, no CORS, no `/docs`, `/redoc`, `/openapi.json`; `all`
  mounts everything (local, tests).
- `app/services/maintenance.py` (new): `sweep(session)` and `cleanup(session, storage)` return counts; both CLIs and
  both endpoints call them (CLI behavior and exit codes unchanged).
- `app/routes/internal.py`: `POST /internal/maintenance/sweep` and `/cleanup` on the existing internal router (router
  dependency `require_internal_auth`, so auth runs first; no body or params). 200 `{"status":"ok", counts...}`; 503
  `{"status":"retry"}` on a DB error, a failed re-enqueue or a failed R2 delete (logged at ERROR); 401 auth. Work runs
  in the threadpool with its own session.
- `app/routes/health.py`: `/health` returns `{"status":"healthy","version":"1.0.0",timestamp}` without touching the DB;
  `/readyz` (sync handler, out of the schema) runs `SELECT 1`, 200 `{"status":"ready"}` or 503 `{"status":"unavailable"}`.
- C1: the three `set_cookie` calls for `imageopz_sid` (`app/routes/session.py` set and clear,
  `app/services/quota_context.py`) use `settings.session_cookie_secure()`.
- Q2-B: no app change needed. Every route resolves the session id as header first, then cookie, and returns it
  in the body where the UI needs it (session/me, restore, logout).
- `docs/openapi.json` regenerated: only the `/health` description texts changed (paths and schemas identical;
  `/readyz` excluded; api and all roles produce the same schema, tested).

Tests (new): `test_service_roles.py`, `test_maintenance_endpoints.py`, `test_health.py`,
`test_session_cookie_secure.py`, `test_header_only_session.py`; `conftest.py` gains `make_client(**env)` (sets env,
reloads config/models/main, restores and reloads at teardown). No existing test or assertion was changed.
Failing-first: before the fix, 22 of the new tests failed (roles, maintenance, H1, C1 staging). The 3 header-only
tests failed only because of a test-harness bug: httpx's `cookies` setter copies the jar and drops its policy. After
fixing the harness they passed without app changes, which is the Q2-B finding.

Docs
- ADR-005 amendment (Q1): Scheduler -> worker endpoints, migrations stay a Cloud Run Job, CLIs for manual runs,
  route split, rollback.
- DEPLOY-PLAN: new section 0 (Q1-Q11 decisions, Q6 cheap bounds, Q8 kill switch description); services table
  (roles, worker max 1); Cloud Run Jobs (migrate only); Scheduler (OIDC to the worker, */5 sweep, hourly cleanup);
  queues (payments 7 days, jobs concurrency 1); route exposure and health; IAM (worker enqueues; scheduler invokes
  the worker; sweep/cleanup SAs dropped); secrets and config (Q3 origins, Q4 buckets, worker enqueue config,
  allowed emails, CORS credentials); G1 table; cookie decision; cost; checklist items 4, 12, 14, 16, 20; rollback.
- registers: H1, C1, D1 closed; U1 risk accepted (Risks); U2 retagged; new UI1 (UI logout credentials) and R1
  (`/readyz` behind Google's front end, unverified); protected behaviors; lesson (below). AGENTS.md: one Learned rule.
- runbooks/local-run.md: maintenance endpoints, SERVICE_ROLE, health.

### Decisions made here (owner to confirm)
- Q5 conflict: worker max-instances 1 makes "jobs-queue concurrency = worker max - 1" zero. Resolved as worker
  concurrency 2 + jobs queue max-concurrent-dispatches 1, so the second slot stays free for payments and scheduler
  calls. Confirm memory with checklist item 10.
- Cadence: sweep `*/5`, cleanup hourly (U2 as originally written), now inside the free tier.
- `CORS_ALLOW_CREDENTIALS` stays true until T-008b3 fixes the UI logout call (UI1), then false.
- `/health` keeps the existing `HealthCheckResponse` shape (with `timestamp`) so the public schema is unchanged.

### UI check for T-008b3 (read-only, imageopz-ui f6f19ce)
- Persists the session id: yes, `localStorage["imageopz_session_id"]` created on load (`src/App.tsx:311-330`) and
  updated from logout/checkout responses (`src/App.tsx:1228, 2265`).
- Sends the header: `buildAuthHeaders` adds `X-Session-Id` (`lib/imageopzApi.ts:135`); `credentials` follows
  `VITE_IMAGEOPZ_SEND_COOKIES` on every call except `logoutSession` (`lib/imageopzApi.ts:288`, always `include`): UI1.

### Follow-ups (not fixed here)
- T-008b2: scripts 20/30/40/50/65/70 to match section 0 (drop sweep/cleanup jobs and SAs; scheduler OIDC to the worker
  and allowed emails; worker enqueue env and IAM; payments queue 604800 s); `95-kill-switch.sh`; Dockerfile and CI;
  A1 on the api; S4; SECRET_KEY scope; XFF; R1 check in staging.
- T-008b3 (UI): UI1.
- Review NIT kept: on the api role a CORS preflight to `/internal/*` gets 200 from the middleware (no handler; the
  real request is 404, tested).

## Checks
| Check | Result |
|---|---|
| SQLite `pytest -q` | 170 passed, 13 skipped (baseline 130/13) |
| Postgres 16.15 (Homebrew, throwaway cluster) `TEST_DATABASE_URL=... pytest -q` | 183 passed (baseline 143) |
| `alembic -x url=<fresh throwaway Postgres db> upgrade head` then `check` (`IMAGEOPZ_ENV_FILE=""`) | "No new upgrade operations detected"; head 0006 |
| OpenAPI regenerated (`IMAGEOPZ_ENV_FILE="" python scripts/generate_openapi.py`) | diff = `/health` description texts only; `tests/test_service_roles.py::test_committed_openapi_matches_the_app` |
| Network guard | on for both suites; no external attempts |
| Em-dashes in changed files | none |

Incident (minor, recorded in registers Lessons): one bare `alembic check` without `-x url=` and without
`IMAGEOPZ_ENV_FILE=""` resolved DATABASE_URL from the developer `.env`. The command is read-only and reported
"Target database is not up to date". The target is unknown (most likely the local `imageopz.db`); `.env` was not read
or printed by me. Owner: confirm which DATABASE_URL `.env` holds.

## Independent review (subagent, diff acd7b82..bb4a5e3), verbatim
> **MUST: none.** I found nothing that blocks the merge.
>
> **AC1, /internal/* unreachable on api.** Pass. On api, `internal.router` is never included, so no handler exists. I probed with a throwaway script (SERVICE_ROLE=api, valid internal token). These all return 404:
> - methods GET, POST, HEAD, OPTIONS, PUT
> - paths `/internal/maintenance/sweep` and the same with a trailing slash, `/INTERNAL/...`, `//internal/...`, `/internal//...`, `/api/v1/../internal/...`, `/%69nternal/...`, plus `/internal/jobs/process` and `/internal/payments/events`
>
> There are no catch-all routes or mounted sub-apps. "internal" does not appear in the openapi.json of either role. The worker role mounts only `/internal/*`, `/health`, `/readyz`, `/version` and FastAPI's docs routes, with no CORS.
>
> **AC2, maintenance auth.** Pass. Auth is a router-level dependency. The handlers take no body and no params, so nothing runs before auth and no 422 can come first. Tests cover unset, unknown, shared_secret with a missing or wrong token, and OIDC with a wrong email, an unverified email or no bearer. `_with_session` always closes the session, and `run_in_threadpool` is used correctly. Every failure returns the generic `{"status":"retry"}` with no exception text.
>
> **AC3 /health, AC4 header-only flow, AC5 fail-closed role, AC6 OpenAPI.** All pass:
> - /health no longer touches the DB and returns no error text.
> - The header-only flow test really blocks cookies: it sets a jar policy and asserts no Cookie header on every request.
> - `Literal` plus the normalizer rejects `""`, `bogus` and `api,worker`.
> - The openapi.json diff is only the /health description text and the 200 description ("Service is healthy" to "Service is alive"). The api and all roles produce identical schemas, and a test checks this.
>
> **Cookies.** Every `set_cookie` for imageopz_sid uses `session_cookie_secure()`: session.py set and clear, and quota_context.py. There are no other setters or `delete_cookie` calls. The cookie test patches both route modules' stale `settings` objects, so it is not vacuous.
>
> **Module-reload pitfalls.** I checked them and none leads to a vacuous test. Tests patch `internal_auth.settings` (the stale object the code actually reads). `health.get_db` is overridden through the stale route-module function. The stale `internal.db` points at the same session-scoped SQLite file.
>
> **SHOULD**
>
> 1. app/routes/health.py:32-35. `readiness_check` is `async def` but makes a blocking `db_session.execute("SELECT 1")` call.
>    - Scenario: the Supabase pooler stalls or a connect hangs. The DB call then blocks the event loop for up to the driver or pool timeout. During that time /health (liveness) and every other request on the instance stall. Cloud Run may then kill healthy instances or the probes cascade.
>    - Fix: make it plain `def readiness_check(...)` so FastAPI runs it in the threadpool. Consider a short connect or statement timeout too.
>
> 2. tests/test_maintenance_endpoints.py:124 (`test_unauthenticated_maintenance_calls_change_nothing`). This only covers SWEEP and does not set an auth mode.
>    - It inherits the conftest default where INTERNAL_AUTH_MODE is unset, so it only exercises the fail-closed branch. It never covers "valid mode, bad credentials leaves state unchanged", and never covers cleanup.
>    - Fix: parametrize over [SWEEP, CLEANUP] × {unset, shared_secret with a wrong token, oidc with a disallowed email}. Assert the UPLOADING job is unchanged, `enqueuer.calls == []`, `outputs.deleted == []`, and the expired DONE job still has `deleted_at is None`.
>
> **NIT**
>
> 3. app/main.py:83-92. On the api role, a CORS preflight `OPTIONS /internal/jobs/process` from an allowed Origin returns 200 from CORSMiddleware. No handler is reached, so it is harmless, but it slightly muddies the "everything 404s" story. Optionally add an api-role test that pins this down.
>
> 4. app/main.py:75. The worker role still serves `/docs`, `/redoc` and `/openapi.json`, whose schema lists only /health and /version. The service is IAM-protected with internal ingress, so this is low impact. Consider `docs_url=None, redoc_url=None, openapi_url=None` when the role is worker.
>
> 5. app/routes/internal.py:87-119. An overlapping Scheduler retry and a manual CLI run can both run a sweep at once and re-enqueue the same stale job twice. The worker lease (J3) makes this safe. It is worth one line in the runbook.
>
> No findings for: exception-text leaks, session leaks, `run_in_threadpool` misuse, missed cookie paths, SERVICE_ROLE fail-closed, or public OpenAPI drift.

Disposition (bec5f20 and the docs commit): SHOULD 1 fixed (sync handler); SHOULD 2 fixed (parametrized over both
endpoints x unset / wrong shared secret / disallowed OIDC email, asserting no state change); NIT 3 pinned by a test;
NIT 4 fixed (worker serves no docs or schema); NIT 5 is not quite right: the conditional UPDATE on
`enqueue_attempts` already lets only one overlapping sweep claim each job (`requeue_stale_jobs`), so no double
re-enqueue. One sentence added to DEPLOY-PLAN section 2.

## Owner commands
```
git push -u origin feat/deploy-readiness
gh pr create --base main --head feat/deploy-readiness --title "T-008b1 deploy readiness: route roles, maintenance endpoints, H1, C1, header-only sessions"
gh pr checks <n> --watch && gh pr merge <n> --squash --delete-branch
```
After the merge: record the squash SHA and PR number in STATE.md (T-008b2 start).

## Amendment 2026-10-17: /readyz renamed to /ready
Basis: Cloud Run known issues, "Reserved URL paths": "You can't use the following URL paths: ... Some paths ending
with `z`. To prevent conflicts with reserved paths, we recommend avoiding all paths that end in `z`"
(https://docs.cloud.google.com/run/docs/known-issues). This settles registers R1 for Python without a staging check.

- `app/routes/health.py`: route `/ready` (handler unchanged), `/health` docstring; `docs/openapi.json` regenerated
  (only that docstring changes; `/ready` stays out of the schema).
- Tests changed deliberately for the rename: `tests/test_health.py` (paths and test names `test_ready_*`),
  `tests/test_service_roles.py` (api smoke path, worker route list). New `test_readyz_is_not_served` pins the old
  path as 404.
- Docs: DEPLOY-PLAN (routes table, SERVICE_ROLE, Health, smoke test, T-008b list item 14, citation R45), registers
  H1 and R1, runbooks/local-run.md. Earlier sections of this file are left as written (append-only).
- Follow-up (out of scope, R1, T-008b2): the Go payments ingress still serves `/healthz` and `/readyz`
  (`services/payments-go/internal/httpapi/httpapi.go`, its tests, `scripts/e2e-direct.sh`, README).
- Checks: SQLite 171 passed / 13 skipped; Postgres 16.15 (Homebrew) 184 passed.
