---
project: imageopz-server
updated: 2026-10-15 by Claude Code (task T-006b)
---

# STATE - ImageOpz

## Current objective
Migrate ImageOpz to GCP Cloud Run + Supabase Postgres, with a Go payments ingress as a portfolio proof,
before applying to Sticker Mule on Mon 19 Oct 2026.

## Accepted baseline
- `main` @ `6f757a4` (PR #13). CI checks: `pytest (sqlite)`, `pytest (postgres)`; payments-go workflow is path-filtered.
- Branch `feat/presigned-uploads` (T-006b, two local commits, not pushed): SQLite 106 passed/7 skipped; Postgres 16.15 (Homebrew) 113 passed; `alembic heads` = 0004.
- Deployed: none; ImageOpz offline. The migrations have never run against Supabase.
- Env: Python 3.11 venv `~/.venvs/imageopz` (rebuilt by the owner, libmagic installed); no Docker; throwaway Postgres from Postgres.app or Homebrew (runbooks/local-postgres.md).

## Done (PR, squash SHA)
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
| T-006b presigned uploads backend, phase 1 (spike, expand) | not pushed | local: see tasks/T-006b-presigned-uploads.md |

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
| T-006b | Presigned uploads backend, phase 1 | done locally; owner pushes and opens the PR |
| T-006b2 | Remove `/optimize`, local-disk upload code and `UPLOAD_DIR`, Alembic 0005 drop `jobs.upload_path`, regenerate OpenAPI, update runbooks/local-run.md | next |
| T-006c | Frontend upload change in imageopz-ui (contract: contracts/uploads-api.md) | open (uploads token scoped, U6 done) |
| T-DOCS-UI | Docs for imageopz-ui (other repo) | open |
| T-007 | Cloud Tasks worker, remove Redis, schedule the upload sweeper (+ runbooks/local-run.md, drop monitor_memory.py) | open |
| T-008 | Containers + Cloud Run deploy; checklist in registers.md | open |

## Owner items
- Discard up to three LemonSqueezy checkout objects created by T-006b test runs (registers.md "Incidents").
- Replace the account-wide outputs token with one scoped to `imageopz` and revoke the old one (U15).
- Wipe the Windows machine before any reuse. GitHub Support purge: pending confirmation.

## Top risks / blockers
- Rate limiting lost without Nginx (presign now included, U1); X-Forwarded-For trust (`app/utils/security.py:IpFingerprint`); Supabase Free pauses after 1 week inactivity / 500 MB.
- Go ingress, Cloud Tasks and OIDC never exercised against GCP; docker build only proven in CI.
- Open follow-ups S1, S6, G1, S4, U1-U5, U7-U15 in registers.md.
- Never run install/dev/build/test on uninspected commits, or on UI commits before fd1032e; tests never load .env or reach external hosts (AGENTS.md).

## Standing authorization
None. Each task grants its own; the owner commits and pushes.

## Next bounded step
Owner pushes feat/presigned-uploads and merges after checks; then T-006b2.
