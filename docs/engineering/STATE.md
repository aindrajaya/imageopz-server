---
project: imageopz-server
updated: 2026-10-15 by Claude Code (task T-005)
---

# STATE - ImageOpz

## Current objective
Migrate ImageOpz to GCP Cloud Run + Supabase Postgres, and add a Go payments ingress as a portfolio proof,
before applying to Sticker Mule on Mon 19 Oct 2026.

## Accepted baseline
- `main` @ `3509591` (T-004 squash, PR #7). Earlier: #1-#6. CI at d249de6 green on both legs.
- T-005 working tree on branch feat/payments-go-ingress (uncommitted): SQLite 69 passed/5 skipped, Postgres 16.1 74 passed, `alembic heads` = 0003, `alembic check` clean; Go `go test -race` + integration + golangci-lint v2.14.0 clean (go1.27.2); local E2E (direct mode) passes.
- Deployed: none; ImageOpz offline. The migration has never run against Supabase.
- Env: Python 3.11 venv `~/.venvs/imageopz`; no Docker; Postgres.app only for throwaway clusters (port 5433).

## Done
T-000 to T-004 (see `tasks/`). T-005 implemented, awaiting owner commit/PR (tasks/T-005-payments-go-ingress.md).

## Open tasks
| Task | Scope | Status |
|---|---|---|
| T-005 | Go ingress (services/payments-go) + Alembic 0003 | implemented, uncommitted; owner commands in the task file |
| T-006 | Direct-to-R2 presigned uploads | open |
| T-007 | Cloud Tasks worker, remove Redis | open |
| T-008 | Containers + Cloud Run deploy: ingress service, queue, SAs, Secret Manager, WIF, migration job, Scheduler, XFF fix, LemonSqueezy URL switch | open |

## Decisions (owner, 2026-10-15)
ADR-001..004 accepted. Cloud Run only; Supabase Postgres (pooler: services 6543 transaction mode, migrations 5432 session mode; table-owner role); keep R2;
Cloud Tasks replaces Redis; presigned uploads. Python is the single writer of users/users_quota/orders; Go dedupes in `payments.webhook_deliveries`. Squash merges.

## Top risks / blockers
- Rate limiting lost without Nginx; X-Forwarded-For trust; Supabase Free pauses; LemonSqueezy checkout must stay disabled while offline; gitleaks history scan before public.
- Go ingress and real Cloud Tasks/OIDC never exercised against GCP; docker build only proven in CI.

## Standing authorization
None. Each task grants its own; the owner commits and pushes.

## Next bounded step
Owner commits T-005 (two commits), PR, checks-gated merge; then T-008 planning (T-006/T-007 independent).
