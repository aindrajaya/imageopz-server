# T-005 - Go payments ingress (verify, dedupe, enqueue), Cloud Run-ready, not deployed
Branch feat/payments-go-ingress from main@3509591 (T-004 merged as #7). ADR-004 accepted. Nothing committed (owner commits).

## Handoff
**Baseline -> final:** 3509591 -> 3509591 + uncommitted. Python SQLite 69 passed/4 skipped -> 69/5 (new Postgres-only RLS test skips);
Postgres 16.1 73 -> 74 passed. Go: go1.27.2 darwin/arm64 (already installed, no brew). golangci-lint v2.14.0 (go install, pinned).
Base images (digests resolved from registry metadata, 2026-10-15): golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61;
gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab.
Actions: setup-go v7.0.0, golangci-lint-action v9.3.0 (latest releases). Go diff ~590 non-test lines (<600 limit); Python diff = 0003 + env.py filter + RLS test.

| Criterion | Command | Result |
|---|---|---|
| Reconcile | pytest -q; TEST_DATABASE_URL=... pytest -q (before changes) | 69/4 SQLite (matches STATE); Postgres 73 per STATE, re-run after 0003 = 74 |
| AC1 | go vet; golangci-lint run --build-tags integration; go test -race ./... | 0 issues; all packages ok |
| AC2/AC3/AC4 | handler tests (fake store with commit-visibility) + `go test -race -tags integration` on migrated Postgres | 3x delivery -> 1 row, 1 enqueue; enqueue failure -> 503, 0 rows, retry accepted; 8 concurrent -> 1 row, 1 enqueue; 401 and ignored write nothing |
| AC5 | services/payments-go/scripts/e2e-direct.sh (real FastAPI + Go ingress, shared_secret) | accepted -> Pro active once; replay duplicate; direct API replay duplicate; state unchanged |
| AC6 | alembic upgrade head; check; downgrade base; upgrade; check; heads (Postgres); upgrade head + check (SQLite) | clean, head 0003; SQLite no-op |
| AC7 | yaml.safe_load of payments-go.yml (3 jobs); docker build NOT run (no Docker) | parses; build runs in CI |

## Design notes
- Handler owns claim -> enqueue -> commit through a Store/Tx interface; pgx implements it, a fake models commit visibility.
- Commit failure after enqueue returns 503; retry hits Cloud Tasks ALREADY_EXISTS or Python idempotency.
- env.py: include_schemas=True with include_name allowing only the default schema (excludes payments and any other schema, e.g. Supabase's).
- Unextractable events (bad JSON, missing event/id, NUL, over column length) -> 200 ignored.

## Env vars
PORT (8080), DATABASE_URL, LEMONSQUEEZY_SIGNING_SECRET, ENQUEUE_MODE (cloudtasks|direct, required), TASKS_TARGET_URL (https in cloudtasks),
TASKS_QUEUE, TASKS_SA_EMAIL, TASKS_AUDIENCE (cloudtasks), INTERNAL_SHARED_SECRET (direct). API side: INTERNAL_AUTH_MODE/_AUDIENCE/_ALLOWED_EMAILS per T-004.

## Independent review (subagent, fresh context, read-only; verbatim summary of findings)
MUST: none.
SHOULD: (1) startup probe `SELECT ... LIMIT 0` passes for a non-owner role under RLS -> every insert 503; (2) DB tx held open across the enqueue call, pool of 4 exhaustible, pooler idle-in-transaction risk; (3) NUL in event/type/id not rejected -> Postgres error -> 503 loop; (4) CI never ran `alembic check` or the RLS test; (5) TASKS_TARGET_URL not required to be https in cloudtasks mode.
NIT: misleading "rollback failed" log after failed commit; shutdown 10s < requestTimeout 15s; hard startup failure if DB briefly down; WARN log flood on bad signatures; ReplaceAll(":","_") broader than design; invalid UTF-8 gets enqueued (Python ignores it); CREATE SCHEMA without IF NOT EXISTS and downgrade drops history; workflow lacks timeout-minutes.
Handling: fixed 3 (NUL -> field_invalid + test), 4 (alembic check + RLS pytest in CI integration job), 5 (https check + test), 1 documented in README (owner role), 2 mitigated (MaxConns 8, requestTimeout 8s, direct client timeout 5s), NITs: rollback log, shutdown vs timeout, ReplaceAll narrowed to "order:created", timeout-minutes. Left: startup DB hard-fail (fail closed, intended), WARN log volume, invalid UTF-8 (safe, signed), schema IF NOT EXISTS (strict on purpose).

## Follow-ups / for T-008
Cloud Run service for payments-go; queue creation; service accounts (tasks SA with run.invoker on the API, ingress SA with cloudtasks.enqueuer and act-as); Secret Manager (signing secret, DATABASE_URL); Supabase pooler URL as table owner;
set API INTERNAL_AUTH_MODE=oidc with audience/allowed emails; switch the LemonSqueezy webhook URL; real Cloud Tasks and docker build never exercised locally; consider retry/idle-in-transaction limits on the Supabase pooler; pin `requests` already done (T-004b).

## Owner commands (not run)
git add alembic tests/test_rls_deny_all.py && git commit -m "feat(db): payments.webhook_deliveries (0003)"
git add services .github docs && git commit -m "feat(payments): Go webhook ingress (verify, dedupe, enqueue)"
git push -u origin feat/payments-go-ingress && gh pr create --fill
gh pr checks <n> --watch && gh pr merge <n> --squash
