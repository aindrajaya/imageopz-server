# deploy/gcloud

Reviewable, ordered gcloud scripts for the plan in [DEPLOY-PLAN.md](../../docs/engineering/deploy/DEPLOY-PLAN.md).
Nothing has been run against GCP yet (T-008a, synced with DEPLOY-PLAN section 0 in T-008b2).

- `DRY_RUN=1` (default) prints every command and executes nothing, not even `describe`.
- `DRY_RUN=0` executes. Creates are guarded by `describe` (idempotent re-runs). Queues, scheduler jobs, WIF
  providers and log metrics are updated in place; `add-iam-policy-binding` is idempotent.
- Parameters: `PROJECT_ID`, `REGION`, `ENV` (staging|prod), plus per-script variables listed in each header and in
  `env.example` (non-secret only). Load them with `set -a; . ~/imageopz-<env>.env; set +a` from a copy outside the repo.
- Secret values never pass through these scripts. After `30-secrets.sh`:
  `gcloud secrets versions add imageopz-<name>-<env> --project=PROJECT_ID --data-file=-`
- Compatible with macOS `/bin/bash` 3.2.

| Script | Scope | What |
|---|---|---|
| 00-enable-apis.sh | project | enable APIs |
| 10-artifact-registry.sh | project | Docker repo `imageopz`, cleanup policy (`ar-cleanup-policy.json`, dry-run until `CLEANUP_DRY_RUN=0`) |
| 20-service-accounts.sh | env | one service account per service/job (api, worker, payments, tasks-invoker, migrate, scheduler), no roles |
| 30-secrets.sh | env | secret containers + per-secret accessor bindings |
| 40-queues.sh | env | jobs (1 concurrent) and payments (7-day retries) queues, enqueuer and actAs bindings |
| 50-jobs.sh | env | the migrate job (the only Cloud Run Job, Q1); `EXECUTE_MIGRATE=1` runs it |
| 60-deploy-services.sh | env | worker (max 1, concurrency 2), api, payments by digest (unset digest = keep the running image); startup probes on `/ready` |
| 65-service-iam.sh | env | invoker bindings (worker: tasks-invoker and scheduler; api/payments: allUsers) |
| 70-scheduler.sh | env | Cloud Scheduler -> worker `/internal/maintenance/{sweep,cleanup}` with OIDC (staging created paused) |
| 80-wif.sh | env (+ project pool) | GitHub WIF provider, per-env deployer identity, resource-scoped roles; `WIF_STAGE=registry` (image push only) or `all` |
| 90-observability.sh | env (+ budget once) | log metric, alert policies (incl. payments queue not draining), budget |
| 99-kill-switch.sh | env | `stop` / `resume`: queues, schedules, public ingress (Q8) |
| ci-rollout.sh | env | image rollout used by `.github/workflows/deploy.yml` (migrate, services, smoke test) |

Order (first bring-up, docs/engineering/deploy/STAGING-RUNBOOK.md): 00, 10, 20, 30 (+ secret values), 40,
80 (`WIF_STAGE=registry`), repository variables, `deploy.yml` dispatch with `build_only=true` (digests into the env
file), 50, 60, 80 (`WIF_STAGE=all`), 50 (`EXECUTE_MIGRATE=1`), 65, first CI rollout, 70, 90. Staging first.
Run as `./deploy/gcloud/NN-*.sh`; `bash deploy/gcloud/NN-*.sh` if the executable bit is lost.

## CI deploy (`.github/workflows/deploy.yml`, dormant until T-008c)
The workflow does nothing until these repository variables exist (Settings > Secrets and variables > Actions >
Variables; none of them is a secret). Values come from the `80-wif.sh` output:
- `GCP_WIF_PROVIDER` = `projects/<number>/locations/global/workloadIdentityPools/github/providers/imageopz-server`
- `GCP_PROJECT_ID`, `GCP_REGION`
- `GCP_DEPLOYER_STAGING` = `iz-deployer-staging@<project>.iam.gserviceaccount.com`, `GCP_DEPLOYER_PROD` likewise
Create the GitHub Environment `production` with the owner as required reviewer BEFORE the variables (checklist item
18; otherwise GitHub creates it without a reviewer). Bootstrap: `gh workflow run deploy.yml --ref main -f
build_only=true` builds and pushes both images and prints their digests in the run summary, without deploying. The first
deploy of each job and service is the owner's (50, 60, 65); CI then only swaps images (`ci-rollout.sh`).

## Owner steps outside gcloud (Cloudflare, Q4)
- Create the staging buckets `imageopz-staging` (outputs) and `imageopz-uploads-staging` (uploads) in the R2 dashboard.
  Keep both private (no r2.dev public URL, no custom domain): downloads are presigned GETs from `/status` (T-008c2, D1).
- Create two R2 API tokens with Object Read & Write scoped to one staging bucket each; add the values to
  `imageopz-r2-outputs-*-staging` and `imageopz-r2-uploads-*-staging` (`gcloud secrets versions add ... --data-file=-`).
- Uploads bucket CORS (staging origin and `http://localhost:3000`) and the `uploads/` 1-day lifecycle rule
  (DEPLOY-PLAN.md section 7, U3).

## Old payment tasks (manual check, Q9)
The alert in 90 fires when the payments queue has not drained for 1 h (there is no task-age metric). To look:
`gcloud tasks list --queue=imageopz-payments-<env> --location=<region> --format='table(name.basename(),createTime,dispatchCount,lastAttempt.responseStatus.code)'`.
A task older than a few minutes is retrying; fix the cause before the 7-day limit, then let the queue drain.
