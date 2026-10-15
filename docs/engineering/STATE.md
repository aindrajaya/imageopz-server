---
project: imageopz-server
updated: 2026-10-15 by Claude Code (task T-PRE-006)
---

# STATE - ImageOpz

## Current objective
Migrate ImageOpz to GCP Cloud Run + Supabase Postgres, with a Go payments ingress as a portfolio proof,
before applying to Sticker Mule on Mon 19 Oct 2026.

## Accepted baseline
- `main` @ `9a86ca9` (T-PUB-1, PR #9). CI checks: `pytest (sqlite)`, `pytest (postgres)`; payments-go workflow is path-filtered.
- Last recorded local runs (T-005 tree, unchanged tests since): SQLite 69 passed/5 skipped; Postgres 16.1 74 passed; `alembic heads` = 0003; Go `go test -race`, integration and golangci-lint v2.14.0 clean (go1.27.2).
- Deployed: none; ImageOpz offline. The migrations have never run against Supabase.
- Env: Python 3.11 venv `~/.venvs/imageopz`; no Docker; Postgres.app only for throwaway clusters (port 5433).

## Done (PR, squash SHA)
| Task | PR | SHA |
|---|---|---|
| T-000..T-003 and docs | #1-#6 | f5c1266, f87894e, fee9269, ddb07e6, d249de6, da5ffac |
| T-004 payments activation + internal endpoint | #7 | 3509591 |
| T-005 Go payments ingress + Alembic 0003 | #8 | 0130a52 |
| T-SEC-1 scan (report included in #9), T-PUB-1 cleanup (VPS-era files removed, hosts redacted, LICENSE) | #9 | 9a86ca9 |

## Publication and ruleset (verified 2026-10-15 via gh)
- `aindrajaya/imageopz-server` is PUBLIC; LICENSE all rights reserved; T-SEC-1 scan clean (history has no secrets; local `.env` is ignored).
- Ruleset 25125469 on the default branch (active, no bypass): deletion and non_fast_forward blocked; pull request required (0 approvals); required checks `pytest (sqlite)` and `pytest (postgres)`. payments-go jobs intentionally not required (path-filtered).
- Frontend: `aindrajaya/imageopz-ui` is PUBLIC, default branch `main`, TypeScript.

## Owner confirmations (REPORTED, not verifiable from the repo)
- LemonSqueezy checkout is disabled while offline; no live paid orders, so Postgres starts empty and no manual re-grants are needed.
- Supabase project is in ap-northeast-1 (Tokyo) -> Cloud Run, Cloud Run Jobs and the Cloud Tasks queue in asia-northeast1 (ADR-002 amendment).

## Decisions (owner, 2026-10-15)
ADR-001..004 accepted. Cloud Run only; Supabase Postgres (services via pooler 6543 transaction mode, migrations via 5432 session mode, table-owner role); keep R2;
Cloud Tasks replaces Redis; presigned uploads. Python is the single writer of users/users_quota/orders; Go dedupes in `payments.webhook_deliveries`. Squash merges.

## Open tasks
| Task | Scope | Status |
|---|---|---|
| T-DOCS-3 | Portfolio README (current README has no Mermaid diagram or CI badges) | open |
| T-006a | Read-only recon of both repos (server and imageopz-ui) for presigned uploads | open |
| T-006b | Backend presign endpoint (direct-to-R2) | open |
| T-006c | Frontend upload change in imageopz-ui | open |
| T-007 | Cloud Tasks worker, remove Redis (+ update runbooks/local-run.md, drop monitor_memory.py) | open |
| T-008 | Containers + Cloud Run deploy; checklist in registers.md | open |

## Top risks / blockers
- Rate limiting lost without Nginx; X-Forwarded-For trust (`app/utils/security.py:IpFingerprint`); Supabase Free pauses after 1 week inactivity / 500 MB.
- Go ingress, Cloud Tasks and OIDC never exercised against GCP; docker build only proven in CI.
- Open payments follow-ups S1, S6 and G1, S4 in registers.md.

## Standing authorization
None. Each task grants its own; the owner commits and pushes.

## Next bounded step
Merge this docs PR, then T-006a recon (both repos); T-DOCS-3 can run in parallel.
