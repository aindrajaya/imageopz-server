# deploy/gcloud

Reviewable, ordered gcloud scripts for the plan in [DEPLOY-PLAN.md](../../docs/engineering/deploy/DEPLOY-PLAN.md).
Nothing has been run against GCP yet (T-008a).

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
| 20-service-accounts.sh | env | one service account per service/job, no roles |
| 30-secrets.sh | env | secret containers + per-secret accessor bindings |
| 40-queues.sh | env | jobs and payments queues (J3 retry config), enqueuer and actAs bindings |
| 50-jobs.sh | env | migrate, sweep-uploads, cleanup-expired jobs; scheduler invoker; `EXECUTE_MIGRATE=1` runs migrate |
| 60-deploy-services.sh | env | worker, api, payments by digest |
| 65-service-iam.sh | env | invoker bindings (worker: tasks-invoker; api/payments: allUsers) |
| 70-scheduler.sh | env | Cloud Scheduler -> jobs (staging created paused) |
| 80-wif.sh | env (+ project pool) | GitHub WIF provider, per-env deployer identity, resource-scoped roles |
| 90-observability.sh | env (+ budget once) | log metric, alert policies, budget |

Order: 00, 10, 20, 30 (+ secret values), 40, 50 (EXECUTE_MIGRATE=1), 60, 65, 70, 80, 90. Staging first.
