---
project: imageopz-server
updated: 2026-10-15 by Claude Code (task T-004)
---

# STATE - ImageOpz

## Current objective
Migrate ImageOpz to GCP Cloud Run + Supabase Postgres, and add a Go payments ingress as a portfolio proof,
before applying to Sticker Mule on Mon 19 Oct 2026.

## Accepted baseline
- `main` @ `da5ffac` (code at `d249de6`, PR #5 squash: ci pin ubuntu-24.04, actions/checkout@v7, actions/setup-python@v7).
- CI run 38646026415 (push to main at d249de6): green on both legs (`pytest (sqlite)`, `pytest (postgres)`), no annotations.
- Tests at d249de6: SQLite 32 passed, 4 skipped; Postgres 16.1 (throwaway cluster) 36 passed. T-004 working tree: SQLite 69 passed, 4 skipped; Postgres 73 passed (after T-004c); `alembic heads` = 0002. The expected values in the T-PRE-004 prompt (32/3 and 35) predated T-003b.1 (+1 Postgres-only RLS test).
- `alembic heads`: single head `0001`. `requirements.txt` has no `google-auth` yet (T-004 adds it).
- Merged PRs: #1 f5c1266 (T-001 commits + .gitignore + T-003/T-003b/T-003b.1), #2 f87894e, #3 fee9269, #4 ddb07e6, #5 d249de6.
- Deployed: none; ImageOpz offline (VPS deleted). The migration has never run against Supabase (UNKNOWN beyond local Postgres 16.1).
- Env: Python 3.11 venv `~/.venvs/imageopz`; no Docker; Postgres.app only for throwaway clusters (port 5433).

## Done
T-000, T-001 (squashed into #1), T-002, T-003/T-003b/T-003b.1 (#1), T-DOCS-1 (#3), T-DOCS-2 (#4), T-003c (#5). See `tasks/`.

## Open tasks
| Task | Scope | Status |
|---|---|---|
| T-004 | Payments activation service + internal endpoint (R1-R5) | implemented on branch feat/payments-activation (uncommitted); awaiting owner commit/PR; ADR-004 accepted (owner, 2026-10-15) |
| T-005 | Go ingress service (Cloud Run-ready) | open |
| T-006 | Direct-to-R2 presigned uploads (backend + frontend) | open |
| T-007 | Cloud Tasks worker, remove Redis (+ update runbooks/local-run.md) | open |
| T-008 | Containers + Cloud Run deploy: WIF, Secret Manager, Alembic migration job, Cloud Scheduler cleanup, XFF trusted-proxy fix, max-instances | open |

## Decisions (owner, 2026-10-15)
- ADR-001 payments Go ingress: accepted. ADR-002 hosting Cloud Run: accepted. ADR-003 Postgres + Alembic: accepted.
- Hosting: Cloud Run only; Supabase Postgres; keep Cloudflare R2; Cloud Tasks replaces Redis; direct-to-R2 presigned uploads
  (verified 2026-10-15, Cloud Run quotas: HTTP/1 request max 32 MiB, no limit with HTTP/2); min-instances 0 with a max-instances cap;
  services use the Supabase shared pooler in transaction mode (6543), the migration job uses session mode (5432);
  app and migration job connect as the table-owner role (RLS invariant).
- Payments: Go ingress verifies `X-Signature`, dedupes in its own `payments` schema, enqueues via Cloud Tasks to the API's internal
  endpoint; Python stays the single writer of users/users_quota/orders.
  R1 activation once per order; R2 refund revokes Pro, status "refunded"; R3 non-activation events never overwrite an existing
  status; R4 one transaction per event; R5 no exception text in 500s.
- Data: Postgres starts empty; archived SQLite + `.env` kept outside the repo.
- Squash merges are the repo convention.

## Top risks / blockers
- Rate limiting lost without Nginx (no app-level limiter).
- X-Forwarded-For trust (`app/utils/security.py:IpFingerprint`).
- Supabase Free pauses after 1 week inactivity / 500 MB.
- LemonSqueezy checkout must stay disabled while offline (owner action).
- Repo must pass a full-history secret scan (gitleaks) before going public.
- Replay/refund gaps fixed by T-004 (R1-R5) once merged; internal endpoint contract in tasks/T-004-payments-activation.md.

## Pending owner inputs (not blockers for T-004)
Supabase region; frontend repo location; LemonSqueezy live-order check (re-grant any live paid orders manually);
checkout disabled while offline; gitleaks history scan before going public.

## Standing authorization
None. Each task grants its own authorization; the owner commits and pushes.

## Next bounded step
Owner commits and merges T-004, then T-005 (Go ingress) against the internal endpoint contract.
