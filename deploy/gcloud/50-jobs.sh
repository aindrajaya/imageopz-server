#!/usr/bin/env bash
# 50 - The one Cloud Run Job for ENV: migrate (alembic upgrade head via the Supabase SESSION pooler); optional execution.
# Sweep and cleanup are no longer jobs (Q1): Cloud Scheduler calls the worker's /internal/maintenance/* (70-scheduler.sh).
# Requires 20, 30 (with secret versions). Flags: https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy
# The image's default command starts the server; the job overrides it with `alembic upgrade head` (same image).
#
# Usage:
#   PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] \
#   API_IMAGE=REGION-docker.pkg.dev/PROJECT/imageopz/api@sha256:... [EXECUTE_MIGRATE=1] ./50-jobs.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params
require_var API_IMAGE "see deploy/gcloud/env.example"
case "$API_IMAGE" in *@sha256:*) ;; *) die "API_IMAGE must be pinned by digest (...@sha256:...)" ;; esac

if [ "$ENV" = "prod" ]; then APP_ENVIRONMENT=production; else APP_ENVIRONMENT=staging; fi
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

if [ "${EXECUTE_MIGRATE:-0}" = "1" ]; then
  banner "Execute $(job_migrate) and wait"
  run gcloud run jobs execute "$(job_migrate)" --project="$PROJECT_ID" --region="$REGION" --wait
fi
