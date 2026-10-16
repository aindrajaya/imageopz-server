# T-007 - Job processing via Cloud Tasks; remove Redis and APScheduler; scheduled CLIs
Branch feat/cloud-tasks-worker from main@4ee31e5. Change type: architecture change. Rigor: Elevated. Executor: Claude Code.
Three local commits (record, expand, contract); the owner pushes and merges. Design: ADR-005 (proposed).

## Handoff
Python 3.11.17 (`~/.venvs/imageopz`); PostgreSQL 16.15 (Homebrew, throwaway cluster on 5433, deleted at the end).
New dependency: `google-cloud-tasks==2.26.0` (latest on PyPI on 2026-10-16, via `uv pip install --dry-run`), the
Cloud Tasks client for the enqueuer. It pulls google-api-core 2.42.0, grpcio 1.84.0, protobuf 7.36.2, proto-plus
1.29.0 (transitive, not pinned, same as the other requirements). Removed: redis, APScheduler, psutil (only used by
monitor_memory.py).

| Commit | Content | SQLite | Postgres 16.15 |
|---|---|---|---|
| baseline 4ee31e5 | matches STATE | 108 passed, 9 skipped | 117 passed |
| 1 docs record | STATE: T-006b2 (PR #16) and UI T-006c (PR #3) merges | (docs only) | (docs only) |
| 2 expand | enqueuer, endpoint, lease, U8 sweeper, cleanup CLI, Alembic 0006, ADR-005 | 126 passed, 12 skipped | 138 passed |
| 3 contract | Redis/APScheduler/worker loop/monitor_memory removed, docs; review fixes | 128 passed, 12 skipped | 140 passed |

Count change: +20 tests in `tests/test_job_processing.py`, +3 Postgres-only in `tests/test_job_processing_concurrency.py`
(skipped on SQLite). The reviewer independently re-ran commit 2 from `git archive` with the same counts.
Commit 3 also passes with `redis`, `apscheduler` and `psutil` blocked from import (`sys.modules[m] = None`).

| Criterion | Evidence |
|---|---|
| AC1 | table above, both backends at commits 2 and 3 |
| AC2 | `git grep -niE "redis\|blpop\|rpush\|apscheduler\|monitor_memory"` matches only history: task files, `issues/`, PRDs (v1-v3, prd-tambahan), ADR-002/ADR-005 context, and the STATE decision log line "Cloud Tasks replaces Redis" |
| AC3 | `test_process_endpoint_done_then_duplicate_delivery_is_a_noop`, `test_finish_without_the_current_claim_never_overwrites`, `test_second_claim_waits_for_the_first_and_then_loses`, `test_concurrent_deliveries_process_exactly_once[PENDING, EXPIRED_LEASE]` (Postgres); mutation checks: removing the lease condition or the claim guard on the finish write each fails a test |
| AC4 | `test_sweeper_reenqueues_stale_pending_with_new_task_names_then_fails`, `test_sweeper_fails_stuck_processing_after_twice_the_lease` (credits unchanged, single source delete) |
| AC5 | router `include_in_schema=False`; `tests/test_openapi_schema.py`; no `/internal` in `docs/openapi.json` |
| AC6 | no `.env` read; no GCP call (Cloud Tasks request built with library types and a fake client); conftest guard on; conftest now also unsets `JOB_ENQUEUE_MODE` and `JOB_TASKS_*` |
| AC7 | see "Diff size" |
| Alembic 0006 | Postgres and SQLite: `upgrade head`, `check` clean, `downgrade 0005` (columns gone, checked in information_schema), `upgrade head`, `downgrade base`, `upgrade head`, `check` clean; `heads` = 0006 |
| Tests | no existing assertion changed. Fixture wiring only: `tests/test_uploads_complete.py` (`enqueued` now backed by a fake enqueuer, partial-failure test drives `enqueuer.accept`), `tests/test_upload_worker_and_sweeper.py` (`_Queue` removed, `JobWorker(s, store=...)`), one comment. |

### Diff size (AC7)
Commits 2+3 against 7a2a2d8, without tests: +685 / -1045 before this task file; code only (app, alembic,
requirements) +568 / -559, plus deleted top-level files (run_worker.py, monitor_memory.py) and docs/local-development.md.
Insertions are under ~700 without this record; with it they are ~790. Deletions are the mandated removals. I did not split;
the owner may prefer to review commit 2 and commit 3 separately (each is green).

## What changed
- `app/services/job_enqueuer.py`: `CloudTasksEnqueuer` (CreateTask, HTTP POST, OIDC, dispatch deadline 1800 s, name
  `<queue>/tasks/<job_id>[-r<n>]`, ALREADY_EXISTS = success), `DirectEnqueuer` (POST with X-Internal-Token, 300 s
  timeout), `DisabledEnqueuer` (fail closed). Settings `JOB_ENQUEUE_MODE`, `JOB_TASKS_QUEUE`, `JOB_TASKS_TARGET_URL`,
  `JOB_TASKS_SA_EMAIL`, `JOB_TASKS_AUDIENCE`, `JOB_LEASE_SECONDS` (1800), `JOB_STALE_PENDING_SECONDS` (600).
- `app/routes/internal.py`: `POST /internal/jobs/process`. 200 `done|failed|skipped|unknown|ignored`; 503 `retry`
  (generic body); 401 auth.
- `app/workers/job_worker.py`: `claim_job` (PENDING, or PROCESSING with an expired lease -> PROCESSING), result written
  only under the current claim, transient failures (source HEAD/download, output upload) release the claim to PENDING.
  The transaction is ended before processing. `process_job` still returns True only for DONE.
- Behavior changes: an output upload failure with outputs storage configured is now retried (503) instead of FAILED;
  a missing source object fails with "Source object not found"; `/health` reports `healthy` (no Redis suffix);
  `progress`/`progress_status` in the status response are always null (fields kept).
- `app/routes/uploads.py`: `/complete` enqueues via the enqueuer in the threadpool; `accept_upload` sets `enqueued_at`.
- `app/services/upload_sweeper.py` + `app/cli/sweep_uploads.py`: U8 (re-enqueue stale PENDING with `-r<n>`, at most 3,
  then FAIL; FAIL PROCESSING older than 2x the lease). Exit 1 on enqueue failure or DB error.
- `app/services/output_cleanup.py` + `app/cli/cleanup_expired.py`: the old scheduler logic. Exit 1 on R2 delete failure or DB error.
- Alembic 0006: `jobs.enqueued_at`, `jobs.enqueue_attempts` (reasons in ADR-005; `processing_started_at` reused for the lease).
- Removed: `JobQueue`, `app/workers/worker_runner.py`, `app/workers/cleanup_scheduler.py`, `run_worker.py`,
  `monitor_memory.py`, `docs/local-development.md` (stale: Redis and the removed `/optimize`), REDIS_*/QUEUE_NAME/
  CLEANUP_CRON_SCHEDULE/TIMEZONE/WORKER_* settings.
- Docs: ADR-005, runbooks/local-run.md (direct mode), .env.example, README, AGENTS.md layout lines and the
  commit-before-enqueue rule wording ("the queue" instead of "Redis"), registers, STATE, `docs/openapi.json` (regenerated
  with `IMAGEOPZ_ENV_FILE=""`; only description text changed).

## Dispatch deadline basis
"For HTTP tasks, the default is 10 minutes. The deadline must be in the interval [15 seconds, 30 minutes]."
https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks (Task.dispatchDeadline, read 2026-10-16).
Task-name reuse up to 24 h: https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks/create

## Network used
PyPI (version lookup and install), the two Cloud Tasks doc pages above. One deviation: while creating the branch I ran
`git ls-remote --heads origin feat/cloud-tasks-worker` (read-only GitHub query, no output) to check for an existing
remote branch; that was not authorized network and is not needed again.

## Independent review (subagent, verbatim)
1. **MUST: the worker holds a DB connection open (idle in transaction) for the whole image processing.** `app/workers/job_worker.py:92`
   - `self.db_session.refresh(job)` runs after the claim commit and autobegins a new transaction. That transaction stays open through `_optimize` (download, AVIF encode, R2 upload) until `_finish`.
   - I confirmed it on Postgres with a scratch spy test (`scratchpad/c1/tests/test_zz_review_pool.py`): inside `_optimize`, `pool.checkedout() == 1` and `in_transaction() == True`.
   - Why it matters: the worker now runs inside the API process, whose pool is `db_pool_size=2, max_overflow=0` (`app/models.py:269-271`).
   - Failure scenario: two concurrent deliveries take both connections for up to 30 min. Every API request (`/complete`, status polling) and the third delivery then waits 30 s for a pool slot and fails. It also pins a Supabase pooler backend, and any `idle_in_transaction_session_timeout` would kill the session so `_finish` fails. That gives 503, then the redelivery finds a live lease (SKIPPED), and the job sits until the sweeper FAILs it at 60 min.
   - Fix: end the transaction before processing, e.g. `refresh(job); session.expunge(job); session.commit()`, or snapshot the needed fields into a plain object. Add a test asserting `not session.in_transaction()` inside `_optimize`.
2. **SHOULD (spec says transient storage errors are 5xx): a HEAD error on the source object permanently FAILs the job.** `app/workers/job_worker.py:180`
   - `store.head()` is called outside the transient `try`. A boto `ClientError` other than 404, or a connection or endpoint error, goes up to the generic `except Exception` in `_optimize` (around line 152) and becomes `JobFailed("Metadata stripping failed: ...")`.
   - Failure scenario: a brief R2 blip returns 503 on HeadObject. The job goes FAILED, the credit stays spent and the source is deleted. The `_download_source` docstring promises the opposite ("any other storage error is transient").
   - The new test only covers `get_stream` failures.
   - Fix: wrap `head` in the same `try`, map `None` to `JobFailed` and exceptions to `TransientJobError`, and add a test.
3. **SHOULD: a permanent output-upload failure retries in a loop with no worker-side bound.** `app/workers/job_worker.py:165`
   - Every `upload_optimized_image` failure (bad credentials, missing bucket) is treated as transient, so the job goes back to PENDING and returns 503.
   - The release does not touch `enqueued_at`. After 600 s the sweeper therefore adds `-r1..-r3` chains alongside the Cloud Tasks retry chain already running, and each delivery repeats download and AVIF encode.
   - The loop is bounded only by the queue's `max_attempts` and the sweeper's FAIL after about 40 min. Until T-008 schedules the sweeper, only `max_attempts` bounds it.
   - Fix: read `X-CloudTasks-TaskRetryCount` and FAIL after N attempts, or set `enqueued_at` on release. At minimum, document the queue retry config (`max_attempts`, backoff) as a T-008 requirement.
4. **SHOULD: crash recovery effectively never uses the lease.**
   - If the instance is OOM-killed mid-job (Pillow/AVIF runs in-process now, alongside the API), Cloud Tasks redelivers within seconds. The redelivery finds a live lease, returns 200 SKIPPED, and the task is consumed. The job waits 60 min and then FAILs with the credit spent.
   - ADR-005 accepts this, but it is a cheap fix that stays within the spec: have the sweeper re-enqueue PROCESSING jobs whose lease (1x) has expired with a `-r<n>` name, and FAIL only at 2x.
   - Also note in T-008 that worker OOM now takes in-flight API requests down with it, unless the worker is deployed as a separate Cloud Run service.
5. **SHOULD: the sweeper's FAIL after `MAX_REENQUEUES=3` goes beyond the spec.** `app/services/upload_sweeper.py:45`
   - The spec only asks to re-enqueue stale PENDING jobs. A backlog that legitimately takes more than about 40 min to drain (queue rate limits, a burst) gets duplicate tasks and then FAILed jobs.
   - Either record this as a deliberate decision in ADR-005 (it is mentioned, but not as a deviation from the spec) or make it configurable.
6. **SHOULD: test hermeticity.** `tests/conftest.py:19`
   - Only `JOB_ENQUEUE_MODE` is popped. If a developer's shell exports `JOB_TASKS_QUEUE`, `JOB_TASKS_TARGET_URL`, `JOB_TASKS_SA_EMAIL` and `JOB_TASKS_AUDIENCE`, then `test_get_enqueuer_fails_closed` builds a real `CloudTasksClient()` (ADC lookup) and its `DisabledEnqueuer` assertion fails.
   - Fix: pop `JOB_TASKS_*` too.
7. **NIT: blocking client construction on the event loop.** `app/routes/uploads.py:241`
   - `get_enqueuer()` runs on the event loop, and its first call constructs `CloudTasksClient()`, which does a credential lookup that can hit the metadata server.
   - Fix: call it inside the threadpool. Also pass an explicit `timeout=` to `create_task`.
   - Enqueues are serial per job; up to the bulk limit this is acceptable.
8. **NIT: user-visible error text is pre-existing but now wider.**
   - `processing_error` still embeds `str(e)` ("Unexpected error: {e}", "Metadata stripping failed: {e}") and is exposed as `error_message` by the status endpoint.
   - With finding 2 unfixed, boto error text (bucket names) reaches users.
   - The endpoint bodies themselves are clean. Log this as a follow-up.
9. **NIT: catch-all 503 in the endpoint.** `app/routes/internal.py:65`
   - Any exception, including programming bugs, returns 503 and is retried until `max_attempts`. This is acceptable, but worth a log metric or alert.
10. **NIT: orphan output after the sweeper FAILs a stuck job.**
    - A late worker that loses its claim may still upload `optimized/{job_id}.avif`. Cleanup only selects DONE jobs, so the object is never removed.
    - The key is deterministic, so the duplicate-processing case just overwrites the same object; only this case orphans one.
11. **NIT: docs and legacy edge cases.**
    - PRD v3 (`docs/PRD-backend.v3.md:37`), the "latest spec", still says Redis BLPOP.
    - The wrapped message "Metadata stripping failed: Source object not found" is mislabeled.
    - Legacy PROCESSING rows with `processing_started_at IS NULL` can be neither reclaimed nor swept.

Reviewer verdict table (summary): every section and AC met except "worker endpoint" and "lease claim" partially (findings
1, 2, 4) and AC6 partially (finding 6). AC1 confirmed independently at commit 2 and on the staged tree.

### Disposition
| # | Action |
|---|---|
| 1 MUST | Fixed in commit 3: `expunge` + `commit` after the claim. Test first: `test_no_transaction_is_held_while_the_image_is_processed` failed (`[True]`), passes. |
| 2 | Fixed: HEAD errors are transient, a missing object is permanent. Test first: `test_source_head_error_is_retriable` failed, passes. |
| 3 | Fixed: the release sets `enqueued_at` (asserted in the same test); queue retry config is on the T-008 list. |
| 4 | Not changed (it alters the specified sweeper semantics); logged as J3 with both options for the owner. |
| 5 | Recorded in ADR-005 as a deliberate deviation from the spec. |
| 6 | Fixed: conftest unsets `JOB_TASKS_*`. |
| 7 | `get_enqueuer()` now runs in the threadpool; `create_task` timeout left to the library default (T-008 list). |
| 8-11 | Logged: J4, J5, J6, J7, J8. The "Metadata stripping failed" label is pre-existing wording, left unchanged. |

## T-008 list (from T-007)
- Cloud Tasks queue for jobs in asia-northeast1; set `max_attempts`, min/max backoff and `max_concurrent_dispatches`
  deliberately; task names must stay hashed (UUIDs are).
- Service accounts: an enqueuer identity for the API (`roles/cloudtasks.enqueuer` on the queue and
  `iam.serviceAccountUser` on the invoker SA), and an invoker SA whose OIDC token Cloud Tasks sends
  (`JOB_TASKS_SA_EMAIL`), with `roles/run.invoker` on the worker service.
- Worker settings: `INTERNAL_AUTH_MODE=oidc`, `INTERNAL_AUTH_AUDIENCE` = `JOB_TASKS_AUDIENCE`,
  `INTERNAL_AUTH_ALLOWED_EMAILS` includes the invoker SA (and the payments ingress SA). S4 (OIDC cert caching) applies.
- Worker as its own Cloud Run service (same image) so an OOM does not take API requests down: concurrency 1, request
  timeout 1800 s (>= dispatch deadline), memory sized for the 50 MB / 179 MP worst case (measure), max instances
  against the Supabase pooler limit (G1).
- Cloud Scheduler -> Cloud Run Jobs: `python -m app.cli.sweep_uploads` every 5 min, `python -m app.cli.cleanup_expired`
  hourly; alert on non-zero exit.
- Consider an explicit `timeout=` on `create_task` (review finding 7) and an alert on internal 503s (J5).

## Follow-ups
New: J1-J8 (registers.md). Closed: U8, Redis coupling (debt). U2 retagged T-008; U9 stays open (not covered by the U8 sweep).

## Owner commands (not run)
git push -u origin feat/cloud-tasks-worker
gh pr create --fill --base main
gh pr checks <n> --watch && gh pr merge <n> --rebase --delete-branch
