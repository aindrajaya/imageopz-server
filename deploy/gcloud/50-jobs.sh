#!/usr/bin/env bash
# 50 - Cloud Run Jobs for ENV: migrate (alembic upgrade head via the Supabase SESSION pooler), sweep-uploads,
# cleanup-expired; invoker binding for the scheduler identity; optional migrate execution.
# Requires 20, 30 (with secret versions), 40. Flags: https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy
# Jobs are billed instance-based with a 1-minute minimum per execution (https://cloud.google.com/run/pricing), so
# the schedule cadence is a cost decision (70-scheduler.sh, DEPLOY-PLAN.md "Cost").
#
# Usage:
#   PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] \
#   API_IMAGE=REGION-docker.pkg.dev/PROJECT/imageopz/api@sha256:... \
#   R2_ACCOUNT_ID=... R2_BUCKET_NAME=... R2_CUSTOM_DOMAIN=https://... R2_UPLOADS_BUCKET=... \
#   [EXECUTE_MIGRATE=1] ./50-jobs.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params
for v in API_IMAGE R2_ACCOUNT_ID R2_BUCKET_NAME R2_CUSTOM_DOMAIN R2_UPLOADS_BUCKET; do
  require_var "$v" "see deploy/gcloud/env.example"
done
case "$API_IMAGE" in *@sha256:*) ;; *) die "API_IMAGE must be pinned by digest (...@sha256:...)" ;; esac

if [ "$ENV" = "prod" ]; then APP_ENVIRONMENT=production; else APP_ENVIRONMENT=staging; fi
WORKER_URL="$(run_url "$(svc_worker)")"
TMPDIR_ENV="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_ENV"' EXIT
yaml_kv() { printf "%s: '%s'\n" "$1" "$(printf '%s' "$2" | sed "s/'/''/g")"; }

# --- migrate ----------------------------------------------------------------------------------------------------
# DATABASE_URL here is the SESSION pooler (port 5432) as the table-owner role: Alembic needs a session (DDL, advisory
# locks); the transaction pooler (6543) is for the services (ADR-002, ADR-003, runbooks/db-migrations.md).
# --max-retries=0: a failed migration must stop the rollout, not re-run; Alembic revisions run in one transaction
# on Postgres. --task-timeout=600s: the migrations are small; a hang is a failure.
MIGRATE_ENV="$TMPDIR_ENV/migrate.yaml"
{ yaml_kv ENVIRONMENT "$APP_ENVIRONMENT"; yaml_kv DEBUG false; } > "$MIGRATE_ENV"
banner "Deploy job $(job_migrate)"
cat "$MIGRATE_ENV"
run gcloud run jobs deploy "$(job_migrate)" --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" \
  --service-account="$(sa_email migrate)" \
  --command=alembic --args=upgrade,head \
  --tasks=1 --max-retries=0 --task-timeout=600s \
  --cpu=1 --memory=512Mi \
  --env-vars-file="$MIGRATE_ENV" \
  --set-secrets="DATABASE_URL=$(secret_name database-url-session):latest,SECRET_KEY=$(secret_name secret-key):latest" \
  --labels="app=imageopz,env=${ENV},component=migrate"

# --- sweep-uploads (U2, U8) ---------------------------------------------------------------------------------------
# Expires stale UPLOADING jobs (credit released once), re-enqueues stale PENDING jobs (-r<n>, at most 3), FAILs
# PROCESSING jobs older than 2x the lease. Needs the jobs queue (enqueuer) and the uploads bucket (deletes).
# --max-retries=0: the next scheduled run is the retry.
SWEEP_ENV="$TMPDIR_ENV/sweep.yaml"
{
  yaml_kv ENVIRONMENT "$APP_ENVIRONMENT"
  yaml_kv DEBUG false
  yaml_kv DB_POOL_SIZE 1
  yaml_kv JOB_ENQUEUE_MODE cloudtasks
  yaml_kv JOB_TASKS_QUEUE "$(queue_path "$(queue_jobs)")"
  yaml_kv JOB_TASKS_TARGET_URL "$WORKER_URL"
  yaml_kv JOB_TASKS_SA_EMAIL "$(sa_email tasks-invoker)"
  yaml_kv JOB_TASKS_AUDIENCE "$WORKER_URL"
  yaml_kv JOB_LEASE_SECONDS 1800
  # 3600 (not the 600 default): a legitimate Cloud Tasks backlog (jobs queue concurrency 1-2, a 30-image Pro batch
  # takes up to ~1 h) must not be re-enqueued and then FAILed by the sweeper; also clears the 600 s max backoff.
  # Cost: a genuinely lost enqueue (U8) is recovered after ~1 h instead of ~10 min.
  yaml_kv JOB_STALE_PENDING_SECONDS 3600
  yaml_kv R2_ACCOUNT_ID "$R2_ACCOUNT_ID"
  yaml_kv R2_UPLOADS_BUCKET "$R2_UPLOADS_BUCKET"
} > "$SWEEP_ENV"
banner "Deploy job $(job_sweep)"
cat "$SWEEP_ENV"
run gcloud run jobs deploy "$(job_sweep)" --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" \
  --service-account="$(sa_email sweep)" \
  --command=python --args=-m,app.cli.sweep_uploads \
  --tasks=1 --max-retries=0 --task-timeout=300s \
  --cpu=1 --memory=512Mi \
  --env-vars-file="$SWEEP_ENV" \
  --set-secrets="DATABASE_URL=$(secret_name database-url):latest,SECRET_KEY=$(secret_name secret-key):latest,R2_UPLOADS_ACCESS_KEY_ID=$(secret_name r2-uploads-access-key-id):latest,R2_UPLOADS_SECRET_ACCESS_KEY=$(secret_name r2-uploads-secret-access-key):latest" \
  --labels="app=imageopz,env=${ENV},component=sweep"

# --- cleanup-expired --------------------------------------------------------------------------------------------
# Deletes expired outputs from R2 and marks them deleted. --max-retries=1: an R2 blip gets one more try.
CLEANUP_ENV="$TMPDIR_ENV/cleanup.yaml"
{
  yaml_kv ENVIRONMENT "$APP_ENVIRONMENT"
  yaml_kv DEBUG false
  yaml_kv DB_POOL_SIZE 1
  yaml_kv R2_ACCOUNT_ID "$R2_ACCOUNT_ID"
  yaml_kv R2_BUCKET_NAME "$R2_BUCKET_NAME"
  yaml_kv R2_CUSTOM_DOMAIN "$R2_CUSTOM_DOMAIN"
} > "$CLEANUP_ENV"
banner "Deploy job $(job_cleanup)"
cat "$CLEANUP_ENV"
run gcloud run jobs deploy "$(job_cleanup)" --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" \
  --service-account="$(sa_email cleanup)" \
  --command=python --args=-m,app.cli.cleanup_expired \
  --tasks=1 --max-retries=1 --task-timeout=900s \
  --cpu=1 --memory=512Mi \
  --env-vars-file="$CLEANUP_ENV" \
  --set-secrets="DATABASE_URL=$(secret_name database-url):latest,SECRET_KEY=$(secret_name secret-key):latest,R2_ACCESS_KEY_ID=$(secret_name r2-outputs-access-key-id):latest,R2_SECRET_ACCESS_KEY=$(secret_name r2-outputs-secret-access-key):latest" \
  --labels="app=imageopz,env=${ENV},component=cleanup"

banner "Job invoker bindings (scheduler identity, job-scoped)"
# roles/run.invoker includes run.jobs.run (https://docs.cloud.google.com/run/docs/reference/iam/roles).
for job in "$(job_sweep)" "$(job_cleanup)"; do
  run gcloud run jobs add-iam-policy-binding "$job" --project="$PROJECT_ID" --region="$REGION" \
    --member="serviceAccount:$(sa_email scheduler)" --role=roles/run.invoker
done

if [ "${EXECUTE_MIGRATE:-0}" = "1" ]; then
  banner "Execute $(job_migrate) and wait"
  run gcloud run jobs execute "$(job_migrate)" --project="$PROJECT_ID" --region="$REGION" --wait
fi
