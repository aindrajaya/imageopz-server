# T-006b2 - Remove /optimize and local-disk uploads; fix U14
Branch chore/remove-optimize from main@1dc3d67. Change type: removal + bug fix. Rigor: Standard. Executor: Claude Code.
One local commit; the owner pushes and merges.

## Handoff
Python 3.11.17 (`~/.venvs/imageopz`); PostgreSQL 16.15 (Homebrew, throwaway cluster on 5433, deleted at the end).
The branch `chore/remove-optimize` already existed locally at 1dc3d67 with no commits of its own; it was checked out, not recreated.

| Step | SQLite | Postgres 16.15 |
|---|---|---|
| Baseline 1dc3d67 (matches STATE) | 110 passed, 9 skipped | 119 passed |
| U14 test added, before the fix | the new test fails (`process_job` returned True, job DONE) | - |
| Final tree (network guard on) | 107 passed, 9 skipped | 116 passed |

Count change: -4 removed `/optimize` tests, +1 U14 test.

| Criterion | Evidence |
|---|---|
| Removal | `POST /api/v1/optimize`, its schemas, local-disk streaming, `upload_dir` setting, worker `upload_path` branch and Redis `file:{id}` fallback removed; `quota_context` kept |
| Alembic 0005 | Postgres and SQLite: `upgrade head`, `check` (clean), `downgrade 0004` (column back as nullable VARCHAR(512), checked in information_schema on Postgres), `upgrade head`, `check` (clean), `downgrade base`, `upgrade head`, `check` (clean); `heads` = 0005 |
| U14 | `tests/test_upload_worker_and_sweeper.py::test_worker_fails_job_when_outputs_store_is_not_configured` |
| Tests | only the four tests below removed; no other assertion changed; `tests/conftest.py` no longer sets `UPLOAD_DIR` (environment setup, no assertion) |
| Grep | see below |
| No secrets | `.env` never read; OpenAPI generated with `IMAGEOPZ_ENV_FILE=""` and REDIS_URL on a closed local port |

## What changed
- `app/routes/optimization.py`: `optimize_images` and its imports removed (checkout, status and webhook routes unchanged).
- `app/schemas.py`: `OptimizeImageRequest`, `BulkOptimizeRequest`, `OptimizationResultResponse`, `BulkOptimizeResponse` removed.
- `app/config.py`: `upload_dir` removed (settings ignore extra env vars, so a leftover `UPLOAD_DIR` is harmless).
- `app/models.py`: `Job.upload_path` removed; `alembic/versions/0005_drop_jobs_upload_path.py` drops it (batch mode, so SQLite works).
- `app/main.py`: `/api/v1/optimize` removed from the request-logging path set.
- `app/workers/job_worker.py`: a job without `source_object_key` fails with "Original file not found"; `_get_file_data`
  and `_cleanup_temp_file` (local file and Redis `file:{id}` blob) removed.
- U14: with no outputs storage (`r2_storage is None`) the worker now marks the job FAILED with
  "R2 upload failed: outputs storage not configured" and returns False. The source object is deleted as for any FAILED
  job; no credit is released (same as every other worker failure).
- `app/services/quota_context.py`: docstring only.
- `docs/openapi.json` regenerated. The committed spec was stale since before T-006b: it now also lists
  `/api/v1/uploads/presign`, `/api/v1/uploads/complete` and `/internal/payments/events`.
- Docs: `runbooks/local-run.md` (uploads path and the buckets each process needs), `contracts/uploads-api.md`
  (the only upload path), README (architecture note and roadmap line).

## Removed tests (replacement coverage added in T-006b)
| Removed | Replacement |
|---|---|
| `tests/test_optimize_session_id.py::test_optimize_stores_session_id_on_users_quota` | `tests/test_uploads_presign.py::test_presign_stores_session_id_on_users_quota` |
| `tests/test_pro_credit_cap.py::test_pro_credit_cap_blocks_after_limit` | `tests/test_uploads_presign.py::test_presign_pro_credit_cap_blocks_after_limit` |
| `tests/test_pro_quota_without_token.py::test_pro_quota_allows_more_than_3_without_auth_token` | `tests/test_uploads_presign.py::test_presign_pro_allows_more_than_3_without_auth_token` |
| `tests/test_smoke.py::test_optimize_returns_503_when_queue_unavailable` | `tests/test_uploads_complete.py::test_complete_returns_503_when_queue_unavailable` |

The three files were deleted; `tests/test_smoke.py` keeps `test_health_smoke` (its unused PNG helper and imports went with the removed test).

## Grep
`git grep -nE "optimize_images|/optimize\b|upload_dir|UPLOAD_DIR|upload_path|file:\{"` on the final tree matches only:
- `alembic/versions/0001_baseline.py`, `alembic/versions/0005_drop_jobs_upload_path.py` (Alembic history);
- `docs/engineering/tasks/T-002-cloud-readiness.md`, `docs/engineering/tasks/T-006b-presigned-uploads.md` and this file
  (append-only task records, describing the removed code; not rewritten).

## Follow-ups
None new. Closed: U7 (this task), U12 (endpoint removed), U14 (fixed).

## Owner commands (not run)
git push -u origin chore/remove-optimize
gh pr create --fill --base main
gh pr checks <n> --watch && gh pr merge <n> --squash --delete-branch
