# T-006b - Presigned direct-to-R2 uploads (backend), phase 1: spike + expand
Branch feat/presigned-uploads from main@6f757a4. Rigor: Elevated (security, credits). Executor: Claude Code.
Scope after owner amendment 2: phase 1 only; phase 2 (contract) is T-006b2. Merged as PR #14 (rebase merge); main SHAs below.

## Handoff
**Baseline -> final:** 6f757a4 -> 7810ada (commit 1, refactor) -> fc89b1b (commit 2, feature; adds this file) -> ad32c65 (docs) -> fe7f405 (test fix).
Python 3.11.17 (`~/.venvs/imageopz`); PostgreSQL 16.15 (Homebrew, throwaway cluster on 5433, deleted at the end).

| Step | SQLite | Postgres 16.15 |
|---|---|---|
| Baseline 6f757a4 (matches STATE) | 69 passed, 5 skipped | 74 passed |
| Commit 1 7810ada (index exported, run outside the repo, no .env) | 69 passed, 5 skipped | 74 passed |
| Commit 2 (final tree, network guard on) | 110 passed, 9 skipped | 119 passed |

| Criterion | Evidence (final revision) |
|---|---|
| AC1 spike recorded, objects deleted | table below; `list_objects_v2` on the spike prefix -> KeyCount 0 |
| AC2 phase-1 tests on both DBs | counts above; Postgres-only `tests/test_uploads_concurrency.py` (row-lock interleaving + two concurrent HTTP presigns -> one 201, one 429) |
| AC3 (local-disk removal, /optimize 404) | deferred to T-006b2 by owner (amendment 2, choice a) |
| AC4 migrations | Postgres and SQLite: `upgrade head`, `check` (clean), `downgrade 0003`, `upgrade head`, `check` (clean), `heads` = 0004; CHECK `jobstatus` allows UPLOADING/REJECTED and rejects other values; `test_rls_deny_all.py` passes |
| AC5 no secrets | spike and checks printed statuses and error codes only; no URL, key or signature in code, logs, tests or docs; `.env` never read directly |
| AC6 tests | no pre-existing test file changed except `tests/conftest.py` (environment setup, guard, `fake_store` fixture); no assertion changed or removed |

Non-test diff: commit 1 +313/-232 (verbatim move); commit 2 about +820 code lines plus docs. The ~600-line stop fired after
phase 1; owner chose to split (phase 2 -> T-006b2).

## Phase 0 spike (real R2, bucket imageopz-uploads, prefix uploads/spike-130d9a9ac1f6/)
Ran with the outputs-bucket token (R2_ACCESS_KEY_ID) through a fallback that amendment 2 then removed. Content-Length,
Content-Type and CORS behavior are bucket/R2 properties, so these results stand (owner, amendment 3); one presigned PUT
with the scoped token is part of step 7.

| # | Check | Result |
|---|---|---|
| 1 | PUT, signed Content-Type image/png: correct / image/jpeg / no header | 200 / 403 SignatureDoesNotMatch / 403 |
| 2 | PUT, signed Content-Length 208: body 218 / 50 / 208 | 403 SignatureDoesNotMatch / 403 / 200; stored length 208 |
| 3 | HEAD; GET Range bytes=0-65535 | 200 (ContentLength, ContentType); 206, Content-Range bytes 0-207/208, magic bytes intact |
| 4 | OPTIONS from https://imageopz.aindrajaya.my.id and http://localhost:3000 (UI dev port from ../imageopz-ui/package.json, read only) | 204; Allow-Origin = the origin, Allow-Methods GET, PUT; Allow-Headers content-type; Max-Age 3600 |
| 5 | Unsigned GET on the S3 endpoint; presigned GET | 400 InvalidArgument (R2's answer to an anonymous request; not readable); 200 |
| 6 | Cleanup | all spike objects deleted; prefix lists 0 objects |

Design consequence: R2 enforces the signed Content-Length, so presign binds it (`BIND_CONTENT_LENGTH = True`); HEAD at
`/complete` re-checks size and type.

## Step 7 least-privilege check (R2_UPLOADS_* only) - PASSED (2026-10-15, after the owner rotated tokens)
Earlier attempts were invalid: the uploads key fields held a 64- then a 31-character value (400 InvalidArgument), and
`R2_BUCKET_NAME` briefly pointed at imageopz-uploads. Final configuration: outputs bucket `imageopz` (R2_BUCKET_NAME,
R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY), uploads bucket `imageopz-uploads` (R2_UPLOADS_*), different key ids (owner choice:
two buckets, two tokens).

| Token | Check | Result |
|---|---|---|
| uploads | ListBuckets | 403 AccessDenied |
| uploads | HEAD / PUT on the outputs bucket `imageopz` | 403 / 403 AccessDenied |
| uploads | presigned PUT (Content-Type + Content-Length bound) / HEAD / DELETE on `imageopz-uploads` | 200 / 200 / 204; HEAD afterwards 404 |
| outputs | ListBuckets | 200 (not bucket-scoped, U15) |
| outputs | PUT on `imageopz-uploads` | 200, deleted at once (204) (not bucket-scoped, U15) |
| outputs | presigned PUT / HEAD / DELETE on `imageopz` | 200 / 200 / 204 |

Spike objects left: 0 in both buckets. The presigned PUT with the scoped token confirms the phase 0 results.

## What changed
- Commit 1 `refactor: extract identity and quota resolution into quota_context (no behavior change)`: session id,
  Supabase user, UsersQuota, Pro rehydration and ACTIVATION_PENDING moved verbatim from `optimize_images` into
  `app/services/quota_context.py`; `/optimize` calls it.
- Commit 2 `feat(uploads): ...`:
  - `app/services/upload_store.py`: UploadStore protocol + R2 implementation (s3v4, region auto, S3 endpoint); only
    R2_UPLOADS_* credentials; missing -> `UploadStoreNotConfigured` -> 503 `UPLOADS_UNAVAILABLE`; startup warning in `app/main.py`.
  - `alembic/versions/0004_presigned_uploads.py`: statuses UPLOADING/REJECTED (CHECK `jobstatus` swapped; SQLite via
    batch), columns source_object_key (unique), declared_size_bytes, declared_content_type, upload_expires_at (indexed),
    rejection_reason, credit_pool (owner-approved addition). Downgrade maps UPLOADING/REJECTED rows to FAILED.
  - `app/services/uploads.py`: one conditional UPDATE reserves credits; conditional transitions out of UPLOADING; the
    caller whose transition matched releases the credit from `credit_pool`, in the same transaction; `/complete` checks.
  - `app/routes/uploads.py`: `POST /api/v1/uploads/presign`, `POST /api/v1/uploads/complete` (contract:
    `docs/engineering/contracts/uploads-api.md`). Enqueue failures revert only those jobs PENDING -> UPLOADING; 503 with the per-job list.
  - Worker: claims PENDING -> PROCESSING conditionally; streams the source to a temp file (size capped at the declared
    size), re-checks magic bytes and opens it with only the declared type's decoder, re-checks pixels, processes as
    before, deletes the temp file and, on DONE/FAILED, the source object.
  - Sweeper `app/services/upload_sweeper.py` + `python -m app.cli.sweep_uploads`.
  - `GET /status` exposes `rejection_reason`; schemas gain uploading/rejected.
  - Test isolation (MUST): `IMAGEOPZ_ENV_FILE=""` disables .env loading; conftest sets dummy values for every external
    service variable before any app import; autouse guard blocks non-local DNS/connects, names the host and fails the
    test even if the error is swallowed. Guard run on both suites: no test attempted an external connection.

## Removed tests
None (phase 2 removes the /optimize tests in T-006b2). Replacement coverage already added: `test_presign_stores_session_id_on_users_quota`
(for test_optimize_session_id), `test_presign_pro_credit_cap_blocks_after_limit` (test_pro_credit_cap),
`test_presign_pro_allows_more_than_3_without_auth_token` (test_pro_quota_without_token),
`test_complete_returns_503_when_queue_unavailable` (test_smoke::test_optimize_returns_503_when_queue_unavailable).

## Incident (blameless, also in registers.md)
Early T-006b runs loaded the developer .env because `tests/upload_fakes.py` imported an app module at collection time;
`tests/test_checkout.py` created up to three live LemonSqueezy checkouts (test@example.com, sess_1). Nothing else was
reached; no secret printed; local DB untouched. Fixed by env isolation + network guard; owner discards the checkouts.
Also verified: all 54 in-tree `__pycache__` files (31 compiled under /Users/macintosh) match their sources.

## Follow-ups (registers.md)
U1 presign rate limiting (T-008); U2 sweeper scheduling (T-007/T-008); U3 R2 lifecycle backstop on uploads/;
U4 Pillow pixel limit below MAX_IMAGE_PIXELS (pre-existing); U5 64 KiB header read rejects JPEGs with >64 KiB leading
metadata (reproduced with a 200 KB ICC profile); U6 scoped uploads token + least-privilege check; U7 T-006b2.
U8-U13 from the independent review; U14 worker marks DONE without output storage; U15 outputs token account-wide. Debt: legacy auth_token in presign; SQLite dev DBs need recreating after 0004;
local-disk path until T-006b2.

## Independent review (subagent, fresh context, read-only; findings verbatim)
Review of T-006b (`git diff 6f757a4` plus the untracked files). What I ran: SQLite suite (106 passed, 7 skipped; the skips are the Postgres-only tests); Alembic 0004 on a scratch SQLite DB: upgrade head, `check` clean, downgrade 0003, upgrade again, `check` clean. Postgres leg not run by me.

#### Findings

**MUST-A - the worker does not check the image type, so it is not the "final guard" that D3 requires.** `app/workers/job_worker.py:239-242` with `app/utils/security.py:107,167`.
- The worker opens the source with `Image.open(path)` and no `formats=` restriction. It does not re-check the magic bytes against `declared_content_type`.
- The presigned PUT URL stays valid for 15 min and can be reused. The client can PUT again after `/complete` has passed and the job is queued.
- The signed Content-Length and Content-Type bind only the length and the header, not the bytes.
- Scenario: the client uploads a valid PNG and calls `/complete` (passes). It then re-PUTs a same-length payload with header `image/png` whose bytes are EPS, TGA, PSD or any other format Pillow can decode.
- The worker hands it to that decoder. EPS shells out to Ghostscript if it is installed in the image. At minimum this exposes Pillow's whole decoder surface to untrusted input, which the /optimize path (server-held bytes) never did.
- Fix: in the worker, open with `formats=[<format for the declared type>]` (or re-run `validate_magic_number` against `declared_content_type`). Add a test where the store object is swapped after `/complete`.

**SHOULD-1 - a duplicate delivery can turn a DONE job into FAILED.** `app/workers/job_worker.py:222,230`.
- The worker skips only UPLOADING and REJECTED. The claim to PROCESSING is an unconditional ORM write.
- Duplicates are possible: `rpush` can succeed on the server but raise on the client. `/complete` then reverts the job to UPLOADING, and the client's retry enqueues it again.
- Scenario: delivery 1 processes the job to DONE and `_release_source` deletes the source. Delivery 2 sees DONE, sets PROCESSING, `get_stream` hits NoSuchKey, and the job ends FAILED with an error. The user loses a finished result.
- A narrower variant: the worker's stale PENDING read overwrites a just-reverted UPLOADING job. The sweeper later rejects it and refunds the credit while processing continues.
- Fix: claim with `UPDATE jobs SET status='PROCESSING' WHERE id=? AND status='PENDING'`, and drop the delivery if no row matched. The same conditional style is already used in `services/uploads.py`.

**SHOULD-2 - a job can get stuck PENDING with its credit spent.** `app/routes/uploads.py:237-244`.
- Window 1: the process dies (e.g. Cloud Run SIGTERM) after `db_session.commit()` at line 237 and before `enqueue_job`.
- Window 2: enqueue fails and the revert commit at line 244 then raises (DB blip; this returns 500).
- Either way the job stays PENDING and never queued. A retried `/complete` reports "pending" and never re-enqueues. The sweeper only handles UPLOADING.
- Result: the credit is spent and the job never runs.
- Fix (or log as a follow-up): the sweeper could re-enqueue or fail PENDING upload jobs older than N minutes, or `/complete` could re-enqueue stale PENDING jobs.

**SHOULD-3 - tests missing for the conditional-transition races.**
- Only the credit reservation has a Postgres concurrency test (`tests/test_uploads_concurrency.py`).
- There is no Postgres test for /complete vs sweeper, or for two concurrent /complete calls, both of which must release exactly once.
- There are no worker tests for REJECTED, a duplicate delivery, or a swapped source (MUST-A, SHOULD-1).
- The current code is correct by construction for release-once (conditional UPDATE re-evaluated under READ COMMITTED). The tests would not catch a regression to read-then-write in `_transition`.

**NIT-1 - two concurrent rejectors can deadlock on Postgres.** `app/services/uploads.py:84-89` with `app/services/upload_sweeper.py:30` and `app/routes/uploads.py:231-236`.
- Each rejection locks a job row and then the quota row, all in one transaction, in request or query order.
- Scenario: `/complete` with [B, A] and the sweeper with [A, B] for the same quota. Postgres aborts one with a deadlock error, so either the request returns 500 or the CLI exits.
- No data is corrupted (both are atomic). Fix by sorting ids or committing per job in the sweeper.

**NIT-2 - concurrent /complete can report "pending" for a job that ends up UPLOADING.** `app/routes/uploads.py:222,240-244`.
- Request A accepts and commits, then its enqueue fails. Request B, between those steps, sees PENDING and returns 200 "pending". A then reverts the job to UPLOADING.
- A client that trusts B's answer never retries. The job expires and the credit is refunded, but the image is never processed.

**NIT-3 - ownership check in /complete can differ from /status.** `app/routes/uploads.py:213`.
- /complete checks ownership by `jobs.user_quota_id == ctx.user_quota.id`. `ctx.user_quota` is "the most recently updated quota row for user_id".
- Rehydration can attach several quota rows to one user. If another row is bumped between presign and complete, the user gets 404 on their own jobs.
- `/status` checks `quota.user_id == user.id` instead. Consider matching on `user_id` for authenticated callers. No cross-user hole found: unknown and foreign ids both return 404 and nothing changes.

**NIT-4 - downgrade strands reserved credits.** `alembic/versions/0004_presigned_uploads.py:54`. The downgrade maps UPLOADING to FAILED, so credits reserved for in-flight uploads are never released and the source objects are orphaned. Worth one line in the docstring or runbook.

**NIT-5 - copying `.env.example` makes uploads look configured.** `.env.example:38-39`. The non-empty placeholders make `uploads_configured()` true, so the presign endpoint returns URLs signed with bogus keys instead of 503. The main R2 entries have the same pattern. Leaving them empty would keep the 503 path.

**NIT-6 - small gaps in the test-isolation guard (MUST-1).** `tests/conftest.py:18-44,57-89`.
- `INTERNAL_AUTH_MODE` and `INTERNAL_AUTH_ALLOWED_EMAILS` are not overridden (not secrets). A shell `INTERNAL_AUTH_MODE=oidc` would change test behavior; the guard would still block the fetch.
- `socket.gethostbyname` and UDP `sendto` are not guarded. No current code uses them.
- Session-scoped `test_env` runs before the function-scoped guard. Only the intended TEST_DATABASE_URL is reached there.

**NIT-7 - worker error text reaches clients.** `app/workers/job_worker.py:250`. When the uploads bucket is unconfigured in the worker, `processing_error` becomes "Metadata stripping failed: R2 uploads bucket credentials not configured", which `/status` returns to clients. It contains no secret, but it is config detail.

**NIT-8 - contract overstates "before anything is written".** `docs/engineering/contracts/uploads-api.md` ("checked before anything is written"). `resolve_quota_context` creates and commits a UsersQuota row before tier validation. The test already tolerates this (`quota is None or credits 0`). Wording only.

**NIT-9 - legacy /optimize can overwrite a presign reservation.** `app/routes/optimization.py:147,287`. It writes credit counters as absolute ORM values (read-then-write), so a concurrent /optimize can overwrite a presign reservation (lost update). This goes away with T-006b2 (D6); just noting the interaction.

**NIT-10 - Pro renewal can turn a later release into a free credit.** `app/services/payments.py:376`. Renewal resets `pro_credits_used=0`. A reservation made before the reset and released afterwards decrements credits spent after the renewal. The `>= 1` guard prevents negative counts only.

Already logged in `registers.md`, so not repeated as findings:
- U4: Pillow's own limit throws `DecompressionBombError` above ~179 MP, so 179-250 MP images are rejected `too_many_pixels`. I confirmed this on Pillow 10.4.0.
- U5: a JPEG with more than 64 KiB of metadata gets "Truncated File Read" and is rejected `invalid_image`. I confirmed this.

Verified OK:
- `reserve_credits` is a single conditional UPDATE using coalesce.
- Every transition out of UPLOADING is conditional, and only the caller whose UPDATE matched releases the credit, from `credit_pool`.
- Rows are committed before enqueue. On enqueue failure only the failed jobs are reverted, conditionally PENDING to UPLOADING, and the 503 carries the per-job list.
- If presigning fails, all jobs are rejected and the credits released.
- Storage errors leave jobs UPLOADING, so the sweeper eventually settles them.
- Object keys are `uploads/<uuid>` with no client input. The filename is DB-only.
- The 7810ada extraction is a verbatim move.
- The Alembic CHECK name `jobstatus` matches the model's `conv("jobstatus")`, and the length is 10.

#### Verdicts
- **D1 - OK.** VARCHAR(10) with CHECK `jobstatus`, native_enum=False; SQLite round trip and `check` clean.
- **D2 - OK.** Reserved at presign and released exactly once in code. SHOULD-2 is a separate case where the credit is spent without release, and SHOULD-3 is the missing race tests.
- **D3 - issue.** /complete does HEAD, a 64 KiB range read, magic/MIME and a header-only Pillow read correctly. But the worker is not a real final guard for type (MUST-A).
- **D4 - OK.** Batch-only and idempotent; see NIT-2.
- **D5 - OK.** 900 s URL TTL and a 1800 s complete window, enforced at /complete and by the sweeper.
- **D7 - issue.** Source is read and deleted on DONE/FAILED and the queue is still Redis. But the unconditional claim lets a duplicate delivery overwrite DONE (SHOULD-1).
- **D8 - OK.** Plain function plus `python -m app.cli.sweep_uploads`, no scheduling.
- **AC2 - partly verified.** SQLite passes (106 passed, 7 skipped). I did not run the Postgres leg or the concurrency test.
- **AC4 - partly verified.** I verified down/up and `alembic check` on SQLite. Postgres and the RLS test were not run by me; there is no new table, so RLS should be unaffected.
- **AC5 - OK.** No secret values found in code, tests, docs or logs. The request log omits bodies and presigned URLs.
- **AC6 - OK.** The only pre-existing test file modified is `tests/conftest.py`, with additions only; no assertions were changed.
- **MUST-1 - OK.** `.env` is disabled via `IMAGEOPZ_ENV_FILE=""` before any app import. Every secret in `app/config.py` gets a dummy or empty value. The autouse guard blocks getaddrinfo, connect and connect_ex, names the host, and fails at teardown even if the error is swallowed. Small gaps in NIT-6.
- **MUST-2 - OK.** `uploads_configured()` uses only the account id and R2_UPLOADS_*, with no fallback. Both endpoints return a generic 503, a startup warning is logged, and `test_uploads_return_generic_503_without_uploads_credentials_even_with_output_credentials` covers it.

### Handling
- MUST-A fixed: the worker re-checks magic bytes against `declared_content_type` and opens the source with only that
  type's Pillow decoder (`formats=[...]`, also at `/complete`). Reproduced first: a BMP declared image/png was processed
  to DONE before the fix (`test_worker_rejects_source_swapped_to_another_format_after_complete`).
- SHOULD-1 fixed: conditional claim PENDING -> PROCESSING; duplicate deliveries and UPLOADING/REJECTED jobs are dropped
  (`test_worker_duplicate_delivery_keeps_the_finished_result`, `test_worker_skips_rejected_jobs`; both failed before).
- SHOULD-2 logged as U8 (T-007). SHOULD-3 fixed: Postgres tests for rejection vs sweeper and two concurrent /complete calls.
- NIT-1 fixed (job-id lock order in /complete and the sweeper). NIT-4 documented in 0004. NIT-5 fixed (empty uploads
  placeholders). NIT-6 fixed (INTERNAL_AUTH_MODE/ALLOWED_EMAILS unset; gethostbyname and UDP sendto guarded, tested).
  NIT-8 fixed (contract wording). NIT-2 -> U9, NIT-3 -> U10, NIT-7 -> U11, NIT-9 -> U12, NIT-10 -> U13.
- The reviewer ran SQLite only; the Postgres leg (119 passed, incl. RLS and 4 concurrency tests) and Postgres
  migrations were run by the executor at the final revision.

## Owner commands (not run)
git push -u origin feat/presigned-uploads
gh pr create --fill --base main
gh pr checks <n> --watch && gh pr merge <n> --squash --delete-branch

## Merge (2026-10-15)
- PR #14, rebase merge. main: 7810ada (refactor), fc89b1b (feature), ad32c65 (docs), fe7f405 (test fix).
- fe7f405: CI flake in `test_partial_enqueue_failure_reverts_only_failed_jobs`; it asserted enqueue order, but /complete
  enqueues in job-id order (NIT-1) and ids are random UUIDs. Now asserts per-job counts and that the retry enqueued only
  the failed job; no product code changed. No other new test depends on job-id order. Evidence: the test 30x on SQLite and
  10x on Postgres 16.15 without a failure; full suite SQLite 110 passed, 9 skipped; Postgres 119 passed.
- fe7f405 carries an AI co-author trailer contrary to AGENTS.md; left in place (registers.md "Lessons").
