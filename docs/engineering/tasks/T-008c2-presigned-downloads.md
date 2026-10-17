# T-008c2 - Presigned output downloads (decision D1), private outputs buckets
Branch `feat/presigned-downloads` on main@09d4a66 (T-008c1 merged as PR #22), worktree `imageopz-server-c2`. Change
type: behavior change (API response) + delivery config. Rigor: Elevated (credentials scope, bearer URLs). Executor:
Claude Code. One local commit; the owner pushes and merges.

## Contract (summary)
Owner decision D1 (STAGING-RUNBOOK "Decisions"): option B, presigned GET, in staging and prod. Outputs buckets stay
private (no r2.dev, no custom domain). `GET /api/v1/status/{job_id}` signs a short-lived GET per request for the job's
owner; the worker stores only the object key. Deploy: the api gets the outputs-token secrets and `R2_BUCKET_NAME`;
`R2_CUSTOM_DOMAIN` removed from `60`, `deploy/gcloud/env.example`, `.env.example`; T-008c1's digest and
`TRUSTED_PROXY_HOPS` behavior kept. Docs: DEPLOY-PLAN, STAGING-RUNBOOK Phase 8, registers (D1 decided, U15 per env, UI
follow-up), STATE (T-008c1 = #22, 09d4a66), AGENTS.md Learned (worktrees).
Authorization: read; edit app/*, tests (new file), deploy/gcloud/*, env examples, docs listed above, AGENTS.md; both
test suites (SQLite, throwaway Postgres on 5433), `bash -n`, shellcheck, actionlint, `DRY_RUN=1` staging + prod.
Local commit on this branch only, no co-author trailer. No gcloud mutations, no secrets, no push.

## Reconcile
The code part was written in an earlier session that shared the main checkout with the T-008c1 session (both edited
one working tree; T-008c1's handoff lists these files as "appeared while this task ran"). It was restored into this
worktree uncommitted. Checked at resume: HEAD = main = 09d4a66; modified app/config.py, app/models.py,
app/routes/optimization.py, app/schemas.py, app/services/image_service.py, app/workers/job_worker.py, docs/openapi.json,
deploy/gcloud/30-secrets.sh (content hunk only; index and worktree mode 100755), untracked tests/test_output_downloads.py.
Content reviewed and consistent; no other stray changes. Lesson recorded in AGENTS.md (2026-10-17, worktrees).

## Handoff
Behavior change (deliberate; no existing test assertion edited):
- `GET /status/{job_id}`: `r2_url` is no longer the stored public URL. It is a presigned SigV4 GET on the R2 S3 endpoint,
  valid `R2_OUTPUT_URL_TTL_SECONDS` (default 900), signed fresh on every call, and only when the caller owns the job
  (Supabase account via its quota, or the anonymous session id from `X-Session-Id`, then the cookie), the job is DONE,
  has `r2_object_key`, is not deleted and not past `expiry_date`. Otherwise `null`; a non-owner anonymous caller still
  gets 200 with status (unchanged access rules: foreign accounts keep their 404). Signing failure or no outputs store ->
  `null` plus an error log, never a 500. Response header `Cache-Control: no-store` (the URL is a bearer credential).
- Worker: stores only `r2_object_key`; `r2_url` stays null. `R2Storage.upload_optimized_image` returns
  `(ok, key, error)` (no URL); new `R2Storage.presign_get`; boto3 client pinned to `signature_version="s3v4"`.
- `app/config.py`: `r2_output_url_ttl_seconds` (900); `r2_custom_domain` kept as deprecated Optional (unused) so old
  env files still load. `jobs.r2_url` column deprecated, not dropped (registers O2).
- `docs/openapi.json` regenerated (X-Session-Id header on /status, descriptions); `tests/test_service_roles.py` checks it.

Files changed in this resume:
- `deploy/gcloud/60-deploy-services.sh`: api env block gains `R2_BUCKET_NAME`; api `--set-secrets` gains
  `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` from `r2-outputs-*`; `R2_CUSTOM_DOMAIN` removed from usage, required vars
  and the worker block. Worker unchanged otherwise (already had both). Digest fallback and `TRUSTED_PROXY_HOPS` untouched.
- `deploy/gcloud/30-secrets.sh` (restored): `r2-outputs-*` consumers `api,worker`. Mode 100755 kept.
- `deploy/gcloud/env.example`, `.env.example`: `R2_CUSTOM_DOMAIN` removed; private-bucket note; `.env.example` gains
  `R2_OUTPUT_URL_TTL_SECONDS=900`. `deploy/gcloud/README.md`: keep both staging buckets private.
- DEPLOY-PLAN: header; IAM matrix (api reads the outputs secrets); secrets table; config table (`R2_BUCKET_NAME` api +
  worker, `R2_OUTPUT_URL_TTL_SECONDS`); section 7 "Outputs" (D1 design, no CORS needed: the UI uses `window.open`);
  go-live item 2 (U15 per env) and new 2b (outputs buckets private).
- STAGING-RUNBOOK: env table row removed; 0.4 and 7.4 private buckets; 2.2 outputs token consumers; 4.4 expectations
  (`R2_BUCKET_NAME` in the api block, no `R2_CUSTOM_DOMAIN`); Phase 8.1 download checks (URL shape, per-request
  signing, unsigned URL refused, non-owner `null`, expiry after 15 min, known UI gap); D1 marked decided.
- registers: U15 reworded (per-environment tokens); new O1 (D1 decided, done), O2 (drop `jobs.r2_url` later), UI2
  (refresh links via `/status` before opening); protected behavior row for downloads. Note: the id `D1` in registers
  is the closed T-008b1 scheduler item, so the output-serving decision is recorded as O1.
- STATE.md overwritten (baseline 09d4a66, T-008c0 #21 and T-008c1 #22 in Done, D1, UI2, next step).
- AGENTS.md Learned: worktree rule.

### Checks run
- SQLite: `pytest -q` -> 197 passed, 13 skipped (`tests/test_output_downloads.py`: 16 passed).
- Postgres 16.15 (Homebrew, throwaway cluster on 5433, removed afterwards; 5433 not listening):
  `TEST_DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5433/postgres pytest -q` -> 210 passed.
- `bash -n` on all 14 `deploy/gcloud/*.sh`: ok. `shellcheck -x -P deploy/gcloud deploy/gcloud/*.sh` (0.11.0): clean.
  `actionlint` (1.7.12): clean.
- `DRY_RUN=1` with a gcloud shim that logs and exits 99, placeholder non-secret env without `R2_CUSTOM_DOMAIN`,
  `PROJECT_NUMBER=123456789012`, for `ENV=staging` and `ENV=prod`, the T-008c1 order (00, 10, 20, 30, 40, 80 registry,
  50, 60 with digests, 60 without digests and `TRUSTED_PROXY_HOPS=2`, 80 all, 50 `EXECUTE_MIGRATE=1`, 65, ci-rollout,
  70, 90, 99 stop, 99 resume): 17/17 exit 0 per env, 104 printed `+ gcloud` lines each, 0 shim calls.
  Same harness on the HEAD (09d4a66) scripts: 102; the +2 are the api's two new accessor bindings in `30`. (T-008c1
  reported 109 with its own harness; the counts are not comparable across harnesses.) Rendered `60` diff vs HEAD:
  worker loses `R2_CUSTOM_DOMAIN`; api gains `R2_BUCKET_NAME` and the two outputs secrets; nothing else.
- Negative (both envs): `TRUSTED_PROXY_HOPS=x` and `API_IMAGE=foo:latest` still fail with the T-008c1 errors.

### Not verified
- Real signing against R2 and the R2 responses for an unsigned or expired URL (runbook Phase 8.1 checks them; the
  runbook says "a 4xx, expected 403" for expiry).
- The UI with expiring links (UI2 open): the result list and saved history reuse the last polled URL.

### Follow-ups (not done, out of scope)
- UI2 (imageopz-ui): refresh links via `/status` before opening; drop `r2_url` from the stuck-job signature.
- O2: drop `jobs.r2_url` in a later revision (`app/services/output_cleanup.py` still nulls it).
- `tests/conftest.py` still sets `R2_CUSTOM_DOMAIN` (harmless: the setting is deprecated and unused); remove with O2.
- STATE notes T-008b3 merged on the UI side (imageopz-ui #6, seen locally); registers UI1 and the server
  `CORS_ALLOW_CREDENTIALS=false` flip are still to be reconciled in a later task.

### Owner commands
```sh
git log -1 --stat feat/presigned-downloads
git diff main...feat/presigned-downloads
git push -u origin feat/presigned-downloads
gh pr create --base main --head feat/presigned-downloads --title "T-008c2 presigned output downloads (D1): private outputs buckets, per-request signed /status URLs"
gh pr checks <n> --watch && gh pr merge <n> --squash
```
Cloudflare (owner): keep `imageopz-staging` / `imageopz` private (r2.dev off, no custom domain); U15 per-env tokens
before staging Phase 2.2. Then the runbook from Phase 0, STOP after each phase.
