# Registers

Updated 2026-10-15 by Claude Code (task T-006b).

## Incidents
- 2026-10-15 - Malicious commits were pushed with the owner's credentials from an infected machine (blameless record).
- Server: a VS Code auto-run task, removed by a history rewrite on 2026-10-14. UI: a build-time remote-code loader, removed in fd1032e (imageopz-ui PR #1).
- Response: credentials and secrets rotated, old SSH keys revoked, affected deployments deleted and rebuilt, GitHub Support purge requested,
  secret scanning and push protection enabled, rulesets on both repos.
- Verification: T-SEC-2/2b and gitleaks (history and tree) clean, except one triaged malware indicator in UI history.
- Lesson: treat any build or dev command as code execution (rule in AGENTS.md).
- 2026-10-15 - T-006b test runs reached the live LemonSqueezy API (blameless record). A new test helper imported an app
  module at collection time, before conftest set the test environment, so settings loaded the developer `.env`;
  `tests/test_checkout.py` then created up to three live checkout objects (email test@example.com, session sess_1) in
  three runs. No payment, no other service reached (Supabase JWKS and Google OIDC were faked, no Resend callers, local
  SQLite file untouched), no secret printed.
- Response: tests no longer load `.env` (`IMAGEOPZ_ENV_FILE=""`), conftest gives every external-service variable a
  dummy value before any app import, and an autouse guard blocks and reports any non-local DNS lookup or connect
  (`tests/test_network_guard.py`). With the guard on, both suites showed no external attempts. Owner discards the checkouts.
- Lesson: test isolation must not depend on import order (rule in AGENTS.md).

## Lessons
| Date | Lesson | Rule |
|---|---|---|
| 2026-10-14 | VPS destroyed before a verified backup; the prod DB and `.env` were later recovered by the owner. | Before decommissioning a host, back up and verify a restore. Secrets live in Secret Manager, not host files. |
| 2026-10-15 | fe7f405 carries an AI co-author trailer contrary to AGENTS.md; left in place (main history is protected). | Co-author attribution disabled in Claude Code settings. |

## Debt
| Item | Where | Note |
|---|---|---|
| SQLite legacy DDL path in `init_db` | `app/models.py` (`TODO(debt)`) | Remove once Postgres is the default for local dev. |
| `datetime.utcnow` (naive timestamps, deprecated) | `app/models.py`, `app/routes/*`, workers | Clean up in a dedicated task. |
| Local-disk upload coupling (API and worker share `UPLOAD_DIR`) | `/optimize`, `jobs.upload_path`, worker `upload_path` and Redis `file:{id}` branches | Resolved for the new flow by T-006b (presigned uploads, worker reads the uploads bucket); the old path is removed in T-006b2. |
| Legacy `auth_token` (custom Pro JWT) accepted in the presign body | `app/routes/uploads.py`, `app/services/quota_context.py` | Kept so auth behavior is unchanged; remove with the JWT path. |
| SQLite dev DBs built by `create_all` miss columns added later | `app/models.py` legacy path | Existing `imageopz.db` files need recreating or `alembic upgrade head` after 0004. |

## Risks
| Id | Risk | Fix |
|---|---|---|
| S1 | A concurrent order_created and order_refunded for the same pre-existing order do not serialize on the dedupe key; the refund can read a stale order (no quota link) and leave the order "refunded" while Pro stays active. | Load the order with `.with_for_update()` in `app/services/payments.py:_apply` (or an advisory lock per order id). Add a Postgres test. |
| S6 | Refunding one of several paid orders for the same user keeps Pro (another paid order backs it) but does not recompute expiry or the credit window from the remaining order. | Decide the rule with the owner (keep latest expiry vs recompute from remaining paid orders) in a payments follow-up task. |

## Follow-ups
| Id | Item | Tag |
|---|---|---|
| S4 | Cache Google OIDC certs for internal auth (`app/utils/internal_auth.py` builds a fresh `google_requests.Request()` per call, fetching certs every time). Use a module-level Request with a cached session. | T-008 |
| G1 | Go pool (8 conns) x Cloud Run instances must fit the Supabase pooler limit; set `--concurrency` and `--max-instances` accordingly. | T-008 |
| U1 | Rate-limit `/uploads/presign` per IP (each new session id gets fresh free credits and object writes). | T-008 |
| U2 | Schedule the expired-upload sweeper (`python -m app.cli.sweep_uploads`) every few minutes. | T-007/T-008 |
| U3 | R2 lifecycle rule on `imageopz-uploads` (`uploads/` older than 1 day) as the backstop for best-effort deletes; owner verifies in Cloudflare. | T-008 |
| U4 | Pillow's own limit (89.5 MP warning, 179 MP error) is below MAX_IMAGE_PIXELS (250 MP), so the effective limit is ~179 MP at `/complete` and in the worker (pre-existing). Decide and set `Image.MAX_IMAGE_PIXELS` deliberately. | backlog |
| U5 | `/complete` reads 64 KiB (D3): a JPEG with >64 KiB of metadata before the frame header (e.g. a 200 KB ICC profile) is rejected `invalid_image`. Option: second ranged read up to 1 MiB. | T-006c feedback |
| U6 | DONE 2026-10-15: uploads token limited to `imageopz-uploads` (ListBuckets and outputs bucket 403; own bucket works), T-006b step 7. | - |
| U7 | T-006b2: remove `/optimize`, local-disk code, `UPLOAD_DIR`, Alembic 0005 drops `jobs.upload_path`, regenerate OpenAPI, update `runbooks/local-run.md`. | next |
| U8 | A job can stay PENDING, never queued, credit spent: process dies between the `/complete` commit and enqueue, or the revert commit fails. Sweeper (or `/complete`) should re-enqueue or fail PENDING upload jobs older than N minutes. Cloud Tasks (T-007) changes the enqueue step. | T-007 |
| U9 | Concurrent `/complete` calls: B can answer "pending" for a job that A then reverts to UPLOADING after an enqueue failure; a client trusting B never retries (job expires, credit refunded, not processed). Same fix as U8. | T-007 |
| U10 | `/complete` ownership uses `jobs.user_quota_id == current quota`; with several quota rows per user (rehydration) an authenticated user can get 404 on their own jobs. `/status` matches on `user_id`; align for authenticated callers. | T-006c feedback |
| U11 | Worker `processing_error` strings reach clients via `/status` (e.g. "uploads bucket credentials not configured"); return generic messages, log details. | backlog |
| U12 | Legacy `/optimize` writes credit counters read-then-write and can overwrite a presign reservation; removed with T-006b2. | T-006b2 |
| U14 | Worker marks a job DONE without uploading the result when outputs storage is not configured (`app/workers/job_worker.py`, `if r2_storage:`); it should fail the job (pre-existing). | T-006b2 |
| U15 | The outputs token (R2_ACCESS_KEY_ID) is account-wide: ListBuckets 200 and it can write `imageopz-uploads`. Replace it with a token scoped to `imageopz` only, revoke the old one, re-run the scope check. | before T-008 |
| U13 | A Pro renewal resets `pro_credits_used`; a reservation made before and released after decrements credits spent after the renewal (floor at 0 only). Decide with S6. | payments follow-up |

## Protected behaviors
| Behavior | Enforced by |
|---|---|
| Webhook signature failures return 401 (invalid, missing header, secret unset) | `tests/test_webhook_characterization.py` |
| Successful public webhook response includes `access_token` | `tests/test_webhook_characterization.py`, `tests/test_webhook.py` |
| RLS deny-all on all app tables and `alembic_version` | `tests/test_rls_deny_all.py` (Postgres leg of CI) |
| `jobs.status` stored as member name (`PENDING`, ...) | `tests/test_job_status_storage.py`, `tests/test_uploads_presign.py` (UPLOADING, REJECTED) |
| Upload credits reserved atomically and released exactly once (reject, sweeper, repeats) | `tests/test_uploads_*.py`, `tests/test_upload_worker_and_sweeper.py`, `tests/test_uploads_concurrency.py` (Postgres) |
| Tests never load `.env` or reach external hosts | `tests/conftest.py` guard, `tests/test_network_guard.py` |

## T-008 checklist
- Region: asia-northeast1 for Cloud Run services, Cloud Run Jobs and the Cloud Tasks queue (ADR-002 amendment).
- Run `alembic upgrade head` as a Cloud Run Job through the Supabase session pooler (5432), as the table-owner role.
- Owner verifies in the Supabase dashboard that the Data API does not expose the `payments` schema (only `public` should be exposed).
- G1: size the Go pool (8) x Cloud Run instances against the Supabase pooler limit; set `--concurrency` and `--max-instances`.
- S4: cache Google OIDC certs in `app/utils/internal_auth.py`.
- Ingress and API OIDC settings (audience, allowed emails), queue and service accounts, Secret Manager, LemonSqueezy webhook URL switch (see tasks/T-005-payments-go-ingress.md).
