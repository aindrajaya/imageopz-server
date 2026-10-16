#!/usr/bin/env bash
# 70 - Cloud Scheduler jobs that execute the sweep and cleanup Cloud Run Jobs of ENV (go-live blocker U2).
# Procedure: https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule (HTTP POST to the Cloud Run Admin API
# ...jobs/JOB:run with an OAuth token; "OIDC is generally used except for Google APIs hosted on *.googleapis.com as
# these APIs expect an OAuth token": https://docs.cloud.google.com/scheduler/docs/http-target-auth).
# Requires 20 and 50 (jobs and their invoker binding).
#
# Cost (DEPLOY-PLAN.md "Cost"): each execution bills at least 1 minute of instance time. Defaults: sweep every
# 15 min, cleanup every 6 h. Staging jobs are created and then PAUSED (resume while testing); paused jobs still
# count toward Scheduler's 3 free jobs per billing account.
#
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] \
#   [SWEEP_SCHEDULE='*/15 * * * *'] [CLEANUP_SCHEDULE='17 */6 * * *'] [PAUSE=1|0] ./70-scheduler.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params

SWEEP_SCHEDULE="${SWEEP_SCHEDULE:-*/15 * * * *}"
CLEANUP_SCHEDULE="${CLEANUP_SCHEDULE:-17 */6 * * *}"
if [ "$ENV" = "staging" ]; then PAUSE="${PAUSE:-1}"; else PAUSE="${PAUSE:-0}"; fi
TIME_ZONE="Etc/UTC"   # tz database name; UTC avoids DST surprises

job_run_uri() { echo "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/$1:run"; }

ensure_schedule() {
  local name="$1" job="$2" schedule="$3" verb=create
  if exists gcloud scheduler jobs describe "$name" --project="$PROJECT_ID" --location="$REGION"; then
    verb=update
  fi
  # Retries: none at the scheduler level; the next tick is the retry and the job itself has --max-retries.
  run gcloud scheduler jobs "$verb" http "$name" --project="$PROJECT_ID" --location="$REGION" \
    --schedule="$schedule" --time-zone="$TIME_ZONE" \
    --uri="$(job_run_uri "$job")" --http-method=POST \
    --oauth-service-account-email="$(sa_email scheduler)" \
    --max-retry-attempts=0 \
    --description="imageopz ${ENV}: execute Cloud Run job ${job}"
  if [ "$PAUSE" = "1" ]; then
    run gcloud scheduler jobs pause "$name" --project="$PROJECT_ID" --location="$REGION"
  elif [ "$verb" = "update" ]; then
    # An existing job may have been paused by hand or by a staging run; PAUSE=0 resumes it.
    run gcloud scheduler jobs resume "$name" --project="$PROJECT_ID" --location="$REGION"
  fi
}

banner "Scheduler jobs (pause=$PAUSE)"
ensure_schedule "$(sched_sweep)" "$(job_sweep)" "$SWEEP_SCHEDULE"
ensure_schedule "$(sched_cleanup)" "$(job_cleanup)" "$CLEANUP_SCHEDULE"
