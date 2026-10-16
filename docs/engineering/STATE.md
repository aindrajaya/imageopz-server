---
project: imageopz-server
updated: 2026-10-16 by Claude Code (T-007 start: merge record)
---

# STATE - ImageOpz

## Current objective
Migrate ImageOpz to GCP Cloud Run + Supabase Postgres, with a Go payments ingress as a portfolio proof,
before applying to Sticker Mule on Mon 19 Oct 2026.

## Accepted baseline
- `main` @ `4ee31e5` (PR #16, T-006b2). SQLite 108 passed/9 skipped; Postgres 16.15 (Homebrew) 117 passed; `alembic heads` = 0005 (verified 2026-10-16 at T-007 start). CI checks: `pytest (sqlite)`, `pytest (postgres)`; payments-go workflow is path-filtered.
- imageopz-ui: T-006c merged as PR #3 (`b509117`), followed by an svg fix PR (REPORTED by the owner; number and SHA not recorded here).
- Deployed: none; ImageOpz offline. The migrations have never run against Supabase.
- Env: Python 3.11 venv `~/.venvs/imageopz` (rebuilt by the owner, libmagic installed); no Docker; throwaway Postgres from Postgres.app or Homebrew (runbooks/local-postgres.md).

## Done (PR, main SHA)
| Task | PR | SHA |
|---|---|---|
| T-000..T-003 and docs | #1-#6 | f5c1266, f87894e, fee9269, ddb07e6, d249de6, da5ffac |
| T-004 payments activation + internal endpoint | #7 | 3509591 |
| T-005 Go payments ingress + Alembic 0003 | #8 | 0130a52 |
| T-SEC-1 scan (report included in #9), T-PUB-1 cleanup (VPS-era files removed, hosts redacted, LICENSE) | #9 | 9a86ca9 |
| Publication, ruleset, region and owner confirmations (docs) | #10 | db2a9bc |
| T-DOCS-3 portfolio README | #11 | 58de6c1 |
| T-SEC-2/2b verification scans (read-only, no PR) | - | - |
| T-SEC-3 worm artifacts removed: imageopz-server .gitignore; imageopz-ui remote-code loader, tracked .env and decoy assets | imageopz-server #12, imageopz-ui #1 | ac7c763, fd1032e |
| T-006a recon of both repos for presigned uploads (report, no PR) | - | - |
| Security incident recorded (registers.md "Incidents") and guardrail rule (AGENTS.md) | #13 | 6f757a4 |
| T-006b presigned uploads backend, phase 1 (spike, expand), rebase merge | #14 | 7810ada, fc89b1b, ad32c65, fe7f405 |
| T-006b merge record (docs) | #15 | 1dc3d67 |
| T-006b2 legacy multipart endpoint and local-disk uploads removed, Alembic 0005, U14 fixed, /internal out of OpenAPI | #16 | 4ee31e5 |
| T-006c frontend presigned uploads (imageopz-ui) | imageopz-ui #3 + svg fix PR | b509117 (svg fix: not recorded) |

## Repo settings (verified 2026-10-15 via gh)
- Both repos PUBLIC; secret scanning and push protection enabled on both.
- `aindrajaya/imageopz-server` ruleset 25125469 on the default branch (active, no bypass): deletion and non_fast_forward blocked; PR required (0 approvals); required checks `pytest (sqlite)` and `pytest (postgres)`.
- `aindrajaya/imageopz-ui` ruleset 25144456 "main protection" (active, no bypass): deletion and non_fast_forward blocked; PR required (0 approvals); required check `check`.

## Owner confirmations (REPORTED, not verifiable from the repo)
- LemonSqueezy checkout is disabled while offline; no live paid orders, so Postgres starts empty.
- Supabase project in ap-northeast-1 (Tokyo) -> Cloud Run, Cloud Run Jobs and the Cloud Tasks queue in asia-northeast1 (ADR-002 amendment).

## Decisions (owner, 2026-10-15)
ADR-001..004 accepted. Cloud Run only; Supabase Postgres (pooler 6543 for services, 5432 for migrations, table-owner role); keep R2;
Cloud Tasks replaces Redis; presigned uploads (T-006b D1-D8: Job reused with UPLOADING/REJECTED, credits reserved at presign and
released once, batch idempotent /complete, URL TTL 15 min, 30 min window, sweeper as CLI). Python is the single writer of
users/users_quota/orders. Squash merges. Uploads use their own R2 token (no fallback to the outputs token).

## Open tasks
| Task | Scope | Status |
|---|---|---|
| T-DOCS-UI | Docs for imageopz-ui (other repo) | open |
| T-007 | Cloud Tasks worker, remove Redis and APScheduler, scheduled CLIs (tasks/T-007-cloud-tasks-worker.md) | in progress (branch feat/cloud-tasks-worker) |
| T-008 | Containers + Cloud Run deploy; checklist in registers.md | open |

## Owner items
- Discard up to three LemonSqueezy checkout objects created by T-006b test runs (registers.md "Incidents").
- Replace the account-wide outputs token with one scoped to `imageopz` and revoke the old one (U15).
- Wipe the Windows machine before any reuse. GitHub Support purge: pending confirmation.

## Top risks / blockers
- Rate limiting lost without Nginx (presign now included, U1); X-Forwarded-For trust (`app/utils/security.py:IpFingerprint`); Supabase Free pauses after 1 week inactivity / 500 MB.
- Go ingress, Cloud Tasks and OIDC never exercised against GCP; docker build only proven in CI.
- Open follow-ups S1, S6, G1, S4, A1, U1-U5, U8-U11, U13, U15 in registers.md.
- Never run install/dev/build/test on uninspected commits, or on UI commits before fd1032e; tests never load .env or reach external hosts (AGENTS.md).

## Standing authorization
None. Each task grants its own; the owner commits and pushes.

## Next bounded step
T-007 on branch feat/cloud-tasks-worker.
