#!/usr/bin/env bash
# 70 - Cloud Scheduler jobs that call the worker's maintenance endpoints of ENV (Q1, ADR-005 amendment; go-live U2):
#   imageopz-sweep-uploads-<env>   POST <worker run.app URL>/internal/maintenance/sweep    every 5 min
#   imageopz-cleanup-expired-<env> POST <worker run.app URL>/internal/maintenance/cleanup  hourly at :17
# Auth: an OIDC token for iz-scheduler-<env>, audience = the worker run.app URL (the app's INTERNAL_AUTH_AUDIENCE).
# "OIDC is generally used except for Google APIs hosted on *.googleapis.com": https://docs.cloud.google.com/scheduler/docs/http-target-auth
# Flags: https://docs.cloud.google.com/sdk/gcloud/reference/scheduler/jobs/create/http
# Requires 20 and 65 (run.invoker on the worker for the scheduler identity); the worker must list that identity in
# INTERNAL_AUTH_ALLOWED_EMAILS (60-deploy-services.sh).
#
# Cost (DEPLOY-PLAN.md "Cost"): request-based billing on the worker, inside the free tier. Staging jobs are created
# and then PAUSED (resume while testing); paused jobs still count toward Scheduler's 3 free jobs per billing account.
#
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] \
#   [SWEEP_SCHEDULE='*/5 * * * *'] [CLEANUP_SCHEDULE='17 * * * *'] [PAUSE=1|0] ./70-scheduler.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params

SWEEP_SCHEDULE="${SWEEP_SCHEDULE:-*/5 * * * *}"
CLEANUP_SCHEDULE="${CLEANUP_SCHEDULE:-17 * * * *}"
if [ "$ENV" = "staging" ]; then PAUSE="${PAUSE:-1}"; else PAUSE="${PAUSE:-0}"; fi
TIME_ZONE="Etc/UTC"   # tz database name; UTC avoids DST surprises
WORKER_URL="$(run_url "$(svc_worker)")"

ensure_schedule() {
  local name="$1" path="$2" schedule="$3" verb=create
  if exists gcloud scheduler jobs describe "$name" --project="$PROJECT_ID" --location="$REGION"; then
    verb=update
  fi
  # Retries: none at the scheduler level; the next tick is the retry (both endpoints are idempotent).
  # --attempt-deadline=300s: a sweep or cleanup answers well within it; the worker's request timeout is 1800 s.
  run gcloud scheduler jobs "$verb" http "$name" --project="$PROJECT_ID" --location="$REGION" \
    --schedule="$schedule" --time-zone="$TIME_ZONE" \
    --uri="${WORKER_URL}${path}" --http-method=POST \
    --oidc-service-account-email="$(sa_email scheduler)" --oidc-token-audience="$WORKER_URL" \
    --attempt-deadline=300s --max-retry-attempts=0 \
    --description="imageopz ${ENV}: POST ${path} on the worker"
  if [ "$PAUSE" = "1" ]; then
    run gcloud scheduler jobs pause "$name" --project="$PROJECT_ID" --location="$REGION"
  elif [ "$verb" = "update" ]; then
    # An existing job may have been paused by hand, a staging run or the kill switch; PAUSE=0 resumes it.
    run gcloud scheduler jobs resume "$name" --project="$PROJECT_ID" --location="$REGION"
  fi
}

banner "Scheduler jobs -> $(svc_worker) (pause=$PAUSE)"
ensure_schedule "$(sched_sweep)" /internal/maintenance/sweep "$SWEEP_SCHEDULE"
ensure_schedule "$(sched_cleanup)" /internal/maintenance/cleanup "$CLEANUP_SCHEDULE"
