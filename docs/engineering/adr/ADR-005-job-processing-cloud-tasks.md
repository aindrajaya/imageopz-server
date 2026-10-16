# ADR-005 Job processing via Cloud Tasks
Status: proposed (T-007, 2026-10-16)
## Context
ADR-002 removes Redis: the worker becomes an HTTP handler on Cloud Run fed by Cloud Tasks. Before T-007 the API
pushed job ids to a Redis list (RPUSH), a separate `run_worker.py` process popped them (BLPOP) and also ran APScheduler
for expired-output cleanup. Cloud Tasks delivers at least once, retries any non-2xx, and may deliver the same task
twice. U8/U9 (registers.md): a job can stay PENDING forever when the process dies between the `/complete` commit and
the enqueue, or when an ambiguous enqueue failure is reverted.
## Decision
- Enqueuer (`app/services/job_enqueuer.py`), chosen by `JOB_ENQUEUE_MODE`, fail closed when unset or unknown:
  - `cloudtasks`: `CreateTask` with an HTTP POST to `JOB_TASKS_TARGET_URL` + `/internal/jobs/process`, JSON body
    `{"job_id": ...}`, OIDC token (`JOB_TASKS_SA_EMAIL`, audience `JOB_TASKS_AUDIENCE`), `dispatch_deadline` 1800 s.
    Task name = `JOB_TASKS_QUEUE` + `/tasks/` + job id, plus `-r<n>` for the n-th deliberate re-enqueue.
    `ALREADY_EXISTS` is success (same pattern as services/payments-go/internal/enqueue).
  - `direct` (local dev): synchronous POST to the same endpoint with `X-Internal-Token`.
  - Tests use a fake enqueuer; the Cloud Tasks request is built with the library's types and a fake client.
- Worker endpoint `POST /internal/jobs/process` on the internal router (`include_in_schema=False`, existing
  `require_internal_auth`). 2xx for every terminal outcome (`done`, `failed`, `skipped` for a duplicate or not-ready
  job, `unknown`, `ignored` for a malformed body); 503 only for retriable failures (database errors, transient storage
  errors: source download, output upload); never exception text. 401 for auth failures.
- Claim with a lease: one conditional UPDATE moves PENDING -> PROCESSING and sets `processing_started_at` (the
  existing column); a PROCESSING job is reclaimed only when `processing_started_at` is older than
  `JOB_LEASE_SECONDS` (1800). Otherwise 2xx no-op. The result is written by a second conditional UPDATE that requires
  `status = PROCESSING AND processing_started_at = <this claim>`, so a slow or duplicate delivery can never overwrite
  DONE or a sweeper FAIL. A transient failure releases the claim (PROCESSING -> PENDING) and answers 503.
- `/uploads/complete` keeps its semantics (commit, then enqueue; only failed jobs revert PENDING -> UPLOADING; 503 with
  the per-job list), now through the enqueuer, off the event loop.
- U8: the sweeper CLI re-enqueues PENDING jobs whose `enqueued_at` is older than `JOB_STALE_PENDING_SECONDS` (600)
  with task name `-r<n>`, at most 3 times, then FAILs them; it FAILs PROCESSING jobs older than 2x the lease. FAILED
  keeps the credit spent, as for every other worker failure (refund policy: follow-up). The attempt counter is bumped
  by a conditional UPDATE before the enqueue, so concurrent sweepers use each name once. The cap of 3 is beyond the
  T-007 spec (which only asked to re-enqueue); it bounds a permanently failing job (e.g. bad output credentials).
  A backlog that legitimately takes longer than ~40 min to drain would get jobs FAILed; revisit if queues are rate-limited.
- The worker ends its transaction after the claim and holds no DB connection while processing (shared small pool).
  A retriable release resets `enqueued_at`, so the sweeper does not add tasks while Cloud Tasks is still retrying.
- New columns (Alembic 0006): `jobs.enqueued_at` (no existing column records when a job was queued; `created_at` is
  the presign time, up to 30 min earlier) and `jobs.enqueue_attempts` (Cloud Tasks keeps an executed task name
  reserved for up to 24 h, so a re-enqueue needs a new, deterministic name).
- APScheduler is replaced by `python -m app.cli.cleanup_expired` (same logic). Both CLIs are idempotent and exit
  non-zero only on real errors. Cloud Scheduler -> Cloud Run Jobs is T-008.
## Dispatch deadline basis
Cloud Tasks REST reference, `Task.dispatchDeadline`: "For HTTP tasks, the default is 10 minutes. The deadline must be
in the interval [15 seconds, 30 minutes]."
https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks (read 2026-10-16).
Task-name reuse: "It can take up to 24 hours ... for the task ID to be released"; hashed (non-sequential) ids are
recommended. https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks/create
## Consequences
- Duplicate and concurrent deliveries process a job at most once (Postgres row lock on the conditional UPDATE).
- A delivery that dies mid-job (instance OOM) is redelivered quickly, finds a live lease and is acknowledged; the job
  then waits for the sweeper, which FAILs it after 2x the lease (60 min). Accepted for simplicity; revisit by answering
  503 while the lease is live if this happens in practice.
- Cloud Run request timeout for the worker must be at least the dispatch deadline (1800 s), and the lease no shorter
  than it, so a reclaimed job's first delivery is gone (T-008).
- Direct mode blocks `/complete` until the job is processed (local dev only).
- The queue's retry config (`max_attempts`, backoff) bounds retries of transient failures before the sweeper does;
  it must be set deliberately in T-008.
## Rollback
Revert the T-007 commits; Alembic 0006 downgrade drops both columns.
## Validation
T-007 AC1-AC7; `tests/test_job_processing.py`, `tests/test_job_processing_concurrency.py` (Postgres).
## Revisit triggers
Jobs failed by the sweeper in production; processing time near the 30 min deadline; need for progress reporting.
