#!/usr/bin/env bash
# 20 - One user-managed service account per service/job for ENV. No roles are granted here: every binding is
# resource-scoped and lives next to its resource (30-secrets, 40-queues, 50-jobs, 65-service-iam, 70-scheduler, 80-wif).
# No keys are ever created (Cloud Run, Cloud Tasks and Cloud Scheduler mint tokens for these identities).
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] ./20-service-accounts.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params

banner "Service accounts"
for role in api worker payments tasks-invoker migrate sweep cleanup scheduler; do
  case "$role" in
    api)           desc="Cloud Run imageopz-api (public API)" ;;
    worker)        desc="Cloud Run imageopz-worker (/internal/*)" ;;
    payments)      desc="Cloud Run imageopz-payments (Go webhook ingress)" ;;
    tasks-invoker) desc="OIDC identity Cloud Tasks sends to the worker" ;;
    migrate)       desc="Cloud Run job imageopz-migrate (alembic)" ;;
    sweep)         desc="Cloud Run job imageopz-sweep-uploads" ;;
    cleanup)       desc="Cloud Run job imageopz-cleanup-expired" ;;
    scheduler)     desc="Cloud Scheduler caller of the cleanup jobs" ;;
  esac
  if ! exists gcloud iam service-accounts describe "$(sa_email "$role")" --project="$PROJECT_ID"; then
    run gcloud iam service-accounts create "$(sa_id "$role")" --project="$PROJECT_ID" \
      --display-name="imageopz ${ENV} ${role}" --description="$desc"
  fi
done
