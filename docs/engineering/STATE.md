---
project: imageopz-server
updated: 2026-10-16 by Claude Code (T-008a)
---

# STATE - ImageOpz

## Current objective
Migrate ImageOpz to GCP Cloud Run + Supabase Postgres, with a Go payments ingress as a portfolio proof,
before applying to Sticker Mule on Mon 19 Oct 2026.

## Accepted baseline
- `main` @ `b595faa` (PR #17, T-007, rebase merge). At T-007 hand-off: SQLite 130 passed/13 skipped; Postgres 16.15 143 passed; `alembic heads` = 0006 (not re-run in T-008a, docs/scripts only). CI checks: `pytest (sqlite)`, `pytest (postgres)`; payments-go workflow is path-filtered.
- Branch `docs/deploy-plan` (T-008a, local commit, not pushed): deploy plan and dry-run gcloud scripts; no GCP resource exists.
- imageopz-ui `main` @ `f6f19ce` (PR #5, T-006d).
- Deployed: none; ImageOpz offline. The migrations have never run against Supabase. No Python Dockerfile exists yet (T-008b).
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
| T-006c frontend presigned uploads (imageopz-ui) | imageopz-ui #3, svg fix #4 | b509117, 49e0c95 |
| T-007 jobs via Cloud Tasks, Redis/APScheduler removed, J3 (rebase merge) | #17 | e140e71, 9b1de12, e0793da, b595faa |
| T-006d upload UX before go-live (imageopz-ui) | imageopz-ui #5 | f6f19ce |

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
| T-008a | Deploy plan (docs/engineering/deploy/DEPLOY-PLAN.md) and dry-run gcloud scripts (deploy/gcloud/), no resources created (tasks/T-008a-deploy-plan.md) | done, pending owner review, push/PR/merge |
| T-008b | Dockerfile, CI build/deploy workflow, SERVICE_ROLE route split, EXPOSE_API_DOCS (A1), S4, H1, SECRET_KEY scope; then staging bring-up with the scripts | next, after the owner answers DEPLOY-PLAN Q1-Q11 |
| ADR-005 | Job processing via Cloud Tasks | proposed; owner review (Q1 may amend it) |

## Owner items
- Discard up to three LemonSqueezy checkout objects created by T-006b test runs (registers.md "Incidents").
- Replace the account-wide outputs token with one scoped to `imageopz` and revoke the old one (U15).
- Wipe the Windows machine before any reuse. GitHub Support purge: pending confirmation.

## Top risks / blockers
- Rate limiting lost without Nginx (presign now included, U1); X-Forwarded-For trust (`app/utils/security.py:IpFingerprint`); Supabase Free pauses after 1 week inactivity / 500 MB.
- Go ingress, Cloud Tasks (both enqueuers) and OIDC never exercised against GCP; docker build only proven in CI (Go image only).
- Scheduled Cloud Run Jobs bill >= 1 min per execution: a 5-min sweep would exceed the ~$10/month budget (DEPLOY-PLAN Q1).
- Open follow-ups S1, S6, G1, S4, A1, U1-U5, U9-U11, U13, U15, J1, J2, J4-J8, H1, C1, D1 in registers.md.
- Never run install/dev/build/test on uninspected commits, or on UI commits before fd1032e; tests never load .env or reach external hosts (AGENTS.md).

## Standing authorization
None. Each task grants its own; the owner commits and pushes.

## Next bounded step
Owner reviews DEPLOY-PLAN.md, answers Q1-Q11, merges T-008a; then T-008b.
