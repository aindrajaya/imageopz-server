---
project: imageopz-server
updated: 2026-10-15 by Claude Code (task T-003c)
---

# STATE — ImageOpz

## Current objective
Migrate ImageOpz to GCP Cloud Run + Supabase Postgres, and add a Go payments ingress as a portfolio proof,
before applying to Sticker Mule on Mon 19 Oct 2026.

## Accepted baseline
- `main` @ `f87894e` (PR #2, README DB section). Code baseline: `f5c1266` (PR #1, squash of feat/postgres-alembic: T-001 commits 00370e7 + b504211, `.gitignore` 3e6c6d8, T-003/T-003b/T-003b.1).
- CI (`backend-tests`, sqlite + postgres legs): green on `main` runs 38638825622 (#1) and 38642599964 (#2); PR run 38638161620 green.
- Tests: webhook characterization, job-status storage, RLS deny-all (incl. `alembic_version`, `alembic/versions/0001_baseline.py:139-140`).
- Deployed: none — ImageOpz offline (VPS deleted). Postgres is empty/unused: the migration has never been run against Supabase (UNKNOWN beyond local Postgres 16.1).
- Env: Python 3.11 venv `~/.venvs/imageopz`; no Docker; Postgres.app only for throwaway clusters (port 5433).

## Open tasks
| Task | Scope | Status |
|---|---|---|
| T-003c | CI hygiene: `runs-on: ubuntu-24.04`; `actions/checkout@v7`, `actions/setup-python@v7` (both `using: node24`, checked in each tag's action.yml) | done (pending merge, branch ci/pin-runner) |
| T-004 | Payments activation service + internal endpoint (R1–R5) | open |
| T-005 | Go ingress service (Cloud Run-ready) | open |
| T-006 | Direct-to-R2 presigned uploads (backend + frontend) | open |
| T-007 | Cloud Tasks worker, remove Redis | open |
| T-008 | Containers + Cloud Run deploy: WIF, Secret Manager, Alembic migration job, Cloud Scheduler cleanup, XFF trusted-proxy fix, max-instances | open |
Done: T-000, T-001, T-002 (recon), T-003 incl. T-003b and T-003b.1. See `tasks/`.

## Recent decisions (owner, 2026-10-14/15)
ADRs in `adr/`: ADR-001 payments Go ingress — proposed, owner to accept · ADR-002 hosting Cloud Run — proposed, owner to accept · ADR-003 Postgres + Alembic — proposed (implemented on `main`).
- Hosting (ADR-002, proposed, leaning adopt): Cloud Run only; Supabase Postgres; keep Cloudflare R2; Cloud Tasks replaces Redis;
  direct-to-R2 presigned uploads (verified 2026-10-15 (Cloud Run quotas: HTTP/1 request max 32 MiB; no limit with HTTP/2)); min-instances 0 with a max-instances cap; services use the Supabase shared pooler
  in transaction mode (6543), migration job uses session mode (5432); app and migration job connect as the table-owner role (RLS invariant).
- Payments (ADR-001 option C, proposed, defaults assumed): Go ingress verifies `X-Signature`, dedupes in its own `payments` schema, enqueues via
  Cloud Tasks to the API's internal endpoint; Python stays the single writer of users/users_quota/orders.
  R1 activation once per order · R2 refund revokes Pro, status "refunded" · R3 non-activation events never overwrite an existing status ·
  R4 one transaction per event · R5 no exception text in 500s.
- Data: Postgres starts empty; archived SQLite + `.env` kept outside the repo; owner checks LemonSqueezy for live paid orders and re-grants manually.
- Squash merges are the repo convention.

## Top risks / blockers
- Rate limiting lost without Nginx (no app-level limiter).
- X-Forwarded-For trust (`app/utils/security.py:IpFingerprint`).
- Supabase Free pauses after 1 week inactivity / 500 MB.
- LemonSqueezy checkout must stay disabled while offline (owner action).
- Repo must pass a full-history secret scan (gitleaks) before going public.
- Replay re-activates Pro, refunds ignored (pinned by tests; fixed by T-004).

## Standing authorization
None. Each task grants its own authorization; the owner commits and pushes.

## Owner inputs pending
Supabase project region · frontend repo location · LemonSqueezy live-order check.

## Next bounded step
Owner merges T-003c (CI must pass on the new runner and action majors), then T-004.
