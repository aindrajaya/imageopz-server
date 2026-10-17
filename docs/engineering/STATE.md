---
project: imageopz-server
updated: 2026-10-17 by Claude Code (T-008b1)
---

# STATE - ImageOpz

## Current objective
Migrate ImageOpz to GCP Cloud Run + Supabase Postgres, with a Go payments ingress as a portfolio proof,
before applying to Sticker Mule on Mon 19 Oct 2026.

## Accepted baseline
- `main` @ `acd7b82` (PR #18, T-008a, squash). At T-008b1 reconcile: SQLite 130 passed/13 skipped; Postgres 16.15 143 passed; `alembic heads` = 0006. CI checks: `pytest (sqlite)`, `pytest (postgres)`; payments-go workflow is path-filtered.
- Branch `feat/deploy-readiness` (T-008b1, local commits, not pushed): SQLite 170 passed/13 skipped; Postgres 16.15 183 passed; `alembic check` clean on a fresh DB. Hand-off: tasks/T-008b1-deploy-readiness.md.
- imageopz-ui `main` @ `f6f19ce` (PR #5, T-006d).
- Deployed: none; ImageOpz offline. No GCP resource exists. Migrations have never run against Supabase. No Python Dockerfile yet (T-008b2).
- Env: Python 3.11 venv `~/.venvs/imageopz`; no Docker; throwaway Postgres from Homebrew (runbooks/local-postgres.md).

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
| T-008a deploy plan and dry-run gcloud scripts | #18 | acd7b82 |

## Repo settings (verified 2026-10-15 via gh)
- Both repos PUBLIC; secret scanning and push protection enabled on both.
- `aindrajaya/imageopz-server` ruleset 25125469 on the default branch (active, no bypass): deletion and non_fast_forward blocked; PR required (0 approvals); required checks `pytest (sqlite)` and `pytest (postgres)`.
- `aindrajaya/imageopz-ui` ruleset 25144456 "main protection" (active, no bypass): deletion and non_fast_forward blocked; PR required (0 approvals); required check `check`.

## Owner confirmations (REPORTED, not verifiable from the repo)
- LemonSqueezy checkout is disabled while offline; no live paid orders, so Postgres starts empty.
- Supabase project in ap-northeast-1 (Tokyo) -> Cloud Run, Cloud Run Jobs and the Cloud Tasks queue in asia-northeast1 (ADR-002 amendment).

## Decisions (owner, 2026-10-15)
ADR-001..004 accepted. Cloud Run only; Supabase Postgres (pooler 6543 for services, 5432 for migrations, table-owner role); keep R2;
Cloud Tasks replaces Redis; presigned uploads (T-006b D1-D8). Python is the single writer of users/users_quota/orders. Squash merges.
Uploads use their own R2 token (no fallback to the outputs token).

## Decisions (owner, 2026-10-17; DEPLOY-PLAN section 0)
| Q | Decision |
|---|---|
| Q1 | Scheduler -> worker `POST /internal/maintenance/{sweep,cleanup}` (done in code, T-008b1; ADR-005 amendment). Migrations stay a Cloud Run Job. |
| Q2 | Option B: run.app URLs + `X-Session-Id` header sessions, cookies off in the UI. |
| Q3 | Staging UI `https://staging.imageopz.aindrajaya.my.id`. |
| Q4 | Separate staging R2 buckets and scoped tokens. |
| Q5 | Provisional sizing; worker max-instances 1 (T-008b1 proposes worker concurrency 2 + jobs queue concurrency 1; confirm). |
| Q6 | Cheap bounds; U1 risk accepted (owner, 2026-10-17; registers Risks). |
| Q7 | Staging Supabase = second project in the same account. |
| Q8 | Budget alerts + manual kill switch (script T-008b2). |
| Q9 | Payments queue retries 7 days + alert. |
| Q10 | Pending owner check (org policy vs `allUsers`). |
| Q11 | Deferred (J1). |

## Open tasks
| Task | Scope | Status |
|---|---|---|
| T-DOCS-UI | Docs for imageopz-ui (other repo) | open |
| T-008b1 | SERVICE_ROLE route split, maintenance endpoints, H1, C1, header-only sessions, DEPLOY-PLAN Q1-Q11 (tasks/T-008b1-deploy-readiness.md) | done, pending owner push/PR/merge |
| T-008b2 | Scripts to match DEPLOY-PLAN section 0 (drop sweep/cleanup jobs, scheduler -> worker, payments 7 d, worker enqueue IAM), kill switch script, Dockerfile, CI build/deploy, A1, S4, SECRET_KEY scope, XFF; staging bring-up | next |
| T-008b3 | imageopz-ui: `logoutSession` honors `VITE_IMAGEOPZ_SEND_COOKIES` (UI1); then `CORS_ALLOW_CREDENTIALS=false` | open |
| ADR-005 | Job processing via Cloud Tasks + 2026-10-17 amendment (maintenance endpoints) | proposed; owner review |

## Owner items
- Discard up to three LemonSqueezy checkout objects created by T-006b test runs (registers.md "Incidents").
- Replace the account-wide outputs token with one scoped to `imageopz` and revoke the old one (U15).
- Wipe the Windows machine before any reuse. GitHub Support purge: pending confirmation.

## Top risks / blockers
- Rate limiting lost without Nginx (presign now included, U1); X-Forwarded-For trust (`app/utils/security.py:IpFingerprint`); Supabase Free pauses after 1 week inactivity / 500 MB.
- Go ingress, Cloud Tasks (both enqueuers) and OIDC never exercised against GCP; docker build only proven in CI (Go image only).
- U1 presign abuse accepted with cheap bounds (Q6). Worker max 1 instance: a Pro batch backlog delays other users.
- Open follow-ups S1, S6, G1, S4, A1, U1-U5, U9-U11, U13, U15, J1, J2, J4-J8, UI1, R1 in registers.md.
- T-008b1 ran one bare `alembic check` that read DATABASE_URL from `.env` (read-only; target unknown, likely local `imageopz.db`); owner to confirm.
- Never run install/dev/build/test on uninspected commits, or on UI commits before fd1032e; tests never load .env or reach external hosts (AGENTS.md).

## Standing authorization
None. Each task grants its own; the owner commits and pushes.

## Next bounded step
Owner reviews T-008b1, pushes, opens the PR and merges after checks; confirms the Q5 worker concurrency-2 proposal; then T-008b2.
