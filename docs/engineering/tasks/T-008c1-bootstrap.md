# T-008c1 - Bootstrap fixes for staging: WIF stages, build-only dispatch, rollback-safe redeploy, script modes
Branch `fix/bootstrap` from main@97add15 (PR #21, T-008c0). Change type: delivery fix. Rigor: Elevated (IAM, CI
identity). Executor: Claude Code. One local commit; the owner pushes and merges.

## Contract (summary)
Fix runbook gaps G1-G3 so staging can bootstrap: `80-wif.sh` `WIF_STAGE=registry|all`; `deploy.yml`
`workflow_dispatch` with `build_only`; `60-deploy-services.sh` takes `TRUSTED_PROXY_HOPS` and the digests from the env
file, keeps the running image when a digest is unset, refuses to create a service without one; executable bit on the
scripts; STAGING-RUNBOOK Phases 3/4 rewritten in the order 80 (registry) -> repository variables -> `build_only`
dispatch -> digests into the env file -> 50 + 60 -> 80 (all) -> migrate execution.
Authorization: read; edit deploy/gcloud/*, deploy.yml, STAGING-RUNBOOK.md, DEPLOY-PLAN.md, this file; `bash -n`,
shellcheck, actionlint, `DRY_RUN=1` runs for staging and prod. No gcloud mutations, no `DRY_RUN=0`, no secrets.

## Reconcile
main = origin/main = 97add15 (T-008c0 merged as #21). The checkout was on `feat/presigned-downloads`, which pointed at
the same commit and was clean at the start; `fix/bootstrap` was created from main.

## Handoff
Files changed:
- `deploy/gcloud/80-wif.sh`: `WIF_STAGE` (default `all`). `registry` = pool, provider (condition unchanged: repository and
  owner by numeric id, `refs/heads/main`, `deploy.yml@refs/heads/main`), `iz-deployer-<env>`, its workloadIdentityUser
  binding, artifactregistry.writer on the repo. `all` = additionally run.developer on the 3 services and the migrate
  job and actAs on api/worker/payments/migrate. With `all` and `DRY_RUN=0` a preflight `describe`s the 3 services and
  the job before any change and dies listing the missing ones ("Deploy them first (50-jobs.sh, 60-deploy-services.sh),
  or run WIF_STAGE=registry ..."). Invalid stage -> error.
- `.github/workflows/deploy.yml`: `workflow_dispatch` with boolean `build_only` (default false). `staging` and
  `production` get `&& inputs.build_only != true` (null on push, so push behavior is unchanged); the vars guard stays on
  every job. The build summary now prints ready-to-paste `API_IMAGE='<registry>/api@sha256:...'` and
  `PAYMENTS_IMAGE='...'` lines (digests passed via `env:`, not inlined into the script). `build_only` still runs
  `tests` first (build needs it).
- `deploy/gcloud/60-deploy-services.sh`: `TRUSTED_PROXY_HOPS` from the environment (default 1, non-negative integer
  check; app accepts `ge=0`). Images: `API_IMAGE` (worker, api) / `PAYMENTS_IMAGE` (payments) when set (must be
  `@sha256:`); when unset, the image the service runs now (`services describe`, `spec.template.spec.containers[0].image`,
  must be `@sha256:`); a service whose image cannot be read and no digest -> error. All three are resolved before
  the first deploy, so a missing one stops the run before any change. Prerequisite line: 50 (job deployed), not
  "executed".
- `deploy/gcloud/env.example`: `API_IMAGE=''`, `PAYMENTS_IMAGE=''` (example values in a comment, "empty again once the
  services exist"), `TRUSTED_PROXY_HOPS='1'`.
- Mode 100755 on the 13 executable scripts in deploy/gcloud (`git update-index --chmod=+x`). `lib.sh` stays 100644:
  it is sourced, has no shebang (G3 said "not lib.sh").
- `docs/engineering/deploy/STAGING-RUNBOOK.md`: `./deploy/gcloud/NN-*.sh` everywhere with the `bash` fallback note;
  Phases 3/4 rewritten (3.1 queues, 3.2 GitHub Environment `production` with required reviewer BEFORE variables, 3.3
  ids, 3.4 `80` registry, 3.5 variables with a re-check of the Environment, 4.1 `build_only` dispatch, 4.2 digests into
  the env file, 4.3 `50` deploy, 4.4 `60`, 4.5 `80` all, 4.6 migrate execution, 4.7 empty the digests again); env-file
  table, Phase 6 hops row, Phase 7.2, D2 (decided), gaps G1-G3 marked fixed, G7 added, risk table references updated.
- `docs/engineering/deploy/DEPLOY-PLAN.md`: header, section 4 WIF note (stages; dispatch matches the same claims),
  section 6 (manual runs), script order. `deploy/gcloud/README.md`: table rows for 60/80, order, CI deploy section.

### Found while doing it (fixed here): G7
T-008c0 Phase 4.1 step 7 ran the first full CI rollout before `65`. `ci-rollout.sh` smoke-tests the api and payments
with `curl -f`; before `65` grants `allUsers` the Cloud Run front end answers 403 on every path, so that `staging` run
would have gone red. The first full CI rollout is now 5.1b (after 65), and 3.5 says: no merges to main from setting
the variables until 5.1b.

Order check for the task's sequence (services before the migration): `/ready` is `SELECT 1` (app/routes/health.py) /
pgx ping (payments-go `ready`), `init_db` creates tables only on SQLite (app/models.py), so the services start on an
empty schema; api/payments take no public traffic before 65 and the worker has no invoker before 65.

### Checks run
- `bash -n` on all 14 `deploy/gcloud/*.sh`: ok.
- `shellcheck -x -P deploy/gcloud deploy/gcloud/*.sh`: clean (no output).
- `actionlint`: clean (no output).
- `DRY_RUN=1` with a gcloud shim on PATH that logs and exits 99 (proves nothing called gcloud), placeholder non-secret
  env, `PROJECT_NUMBER=123456789012`, for `ENV=staging` and `ENV=prod`, in the new order: 00, 10, 20, 30, 40,
  80 `WIF_STAGE=registry`, 50, 60 (digests set), 60 (digests unset, `TRUSTED_PROXY_HOPS=2`), 80 `WIF_STAGE=all`,
  50 `EXECUTE_MIGRATE=1`, 65, ci-rollout, 70, 90, 99 stop, 99 resume: all 17 runs exit 0 per env, 109 printed commands
  each, 0 shim calls.
- Negative cases (both envs): `TRUSTED_PROXY_HOPS=x` -> `error: TRUSTED_PROXY_HOPS must be a non-negative integer`;
  `API_IMAGE=foo:latest` -> `error: API_IMAGE must be pinned by digest (...@sha256:...)`; `WIF_STAGE=x` ->
  `error: WIF_STAGE must be registry or all`.

### DRY_RUN=1 transcript excerpts
```
### Deployer roles: Artifact Registry  (project=imageopz-prod-4821 region=asia-northeast1 env=staging dry_run=1)
+ gcloud artifacts repositories add-iam-policy-binding imageopz --project=imageopz-prod-4821 --location=asia-northeast1 --member=serviceAccount:iz-deployer-staging@... --role=roles/artifactregistry.writer
# WIF_STAGE=registry: Cloud Run roles not granted. After 50 and 60 have created the staging job and services,
# run this script again with WIF_STAGE=all (it re-applies the registry part idempotently).

### Preflight: prod services and migrate job exist (WIF_STAGE=all)  (... env=prod dry_run=1)
# dry-run: would check gcloud run services describe imageopz-api-prod --project=imageopz-prod-4821 --region=asia-northeast1
  (worker, payments, jobs describe imageopz-migrate-prod likewise)

60 with digests unset, TRUSTED_PROXY_HOPS=2 (staging):
# dry-run: API_IMAGE unset, would keep the image imageopz-worker-staging runs now (describe; error if imageopz-worker-staging does not exist)
TRUSTED_PROXY_HOPS: '2'
+ gcloud run deploy imageopz-api-staging ... --image=CURRENT_IMAGE_OF_imageopz-api-staging ...
```

### Not verified (needs DRY_RUN=0, outside this task's authorization)
- `80` `WIF_STAGE=all` preflight die path and `60`'s describe fallback / "cannot be read" die path: logic reviewed and
  shellcheck-clean, never executed. First real exercise: runbook 4.5 (preflight passes) and the first `60` re-run with
  empty digests (Phase 6 or 7.2).
- The `build_only` dispatch and the WIF condition matching a `workflow_dispatch` token: first exercised in 4.1.

### Follow-ups (not done, out of scope)
- F1: `50-jobs.sh` still requires `API_IMAGE`; a stale value re-points the migrate job at an older image (an
  `EXECUTE_MIGRATE=1` run would then fail on an unknown revision rather than downgrade). Same fallback as `60` is the fix.
- F2: `spec.template` is the latest template, which can be a revision that failed to become ready; `60`'s fallback would
  then keep that image. Acceptable now (CI only deploys tested digests); revisit if it bites.
- F3: optional `github.ref == 'refs/heads/main'` guard on the workflow jobs, so a dispatch from another ref skips instead
  of running tests and failing at auth (the WIF condition already refuses it).
- STATE.md not updated: not in this task's authorization (AGENTS.md says it is overwritten each task); owner or the
  next task updates it (T-008c1 done, pending push/PR/merge; next: runbook phases 0-3, then 4).
- Working tree: while this task ran, uncommitted changes that are not part of it appeared in the checkout
  (`app/config.py`, `app/models.py`, `app/routes/optimization.py`, `app/schemas.py`, `app/services/image_service.py`,
  `app/workers/job_worker.py`, `docs/openapi.json`, untracked `tests/test_output_downloads.py`, and a content hunk in
  `deploy/gcloud/30-secrets.sh` citing T-008c2 / D1). Left untouched and not committed; this commit carries only the
  30-secrets.sh mode change.

### Owner commands
```sh
git log -1 --stat fix/bootstrap            # review the one commit
git diff main...fix/bootstrap              # full diff
git push -u origin fix/bootstrap
gh pr create --base main --head fix/bootstrap --title "T-008c1 bootstrap fixes: WIF stages, build-only dispatch, rollback-safe 60, script modes"
gh pr checks <n> --watch && gh pr merge <n> --squash
```
Then the runbook from Phase 0 (or Phase 3 if 0-2 are done), STOP after each phase.
