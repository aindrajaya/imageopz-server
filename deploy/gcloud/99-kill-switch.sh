#!/usr/bin/env bash
# 99 - Kill switch for ENV (Q8): stop spend without deleting anything; `resume` reverses it. Owner-run.
#   stop:   set api and payments to ingress internal (first), pause both Cloud Tasks queues and both Scheduler jobs.
#   resume: set api and payments back to ingress all, resume both queues and the Scheduler jobs (prod; staging stays
#           paused like 70-scheduler.sh unless PAUSE=0). Every step is state-aware (lib.sh set_paused), so re-runs
#           and already-paused staging schedules do not abort the script.
# Why these steps:
# - Queues: "If a queue is paused then the system will stop executing the tasks in the queue until it is resumed.
#   Tasks can still be added when the queue is paused." (https://docs.cloud.google.com/sdk/gcloud/reference/tasks/queues/pause)
#   Tasks are kept up to the 31-day retention (https://docs.cloud.google.com/tasks/docs/quotas). On resume a backlog
#   dispatches at the queue's own limits (jobs 2/s, 1 concurrent).
# - Worker: --max-instances=0 is not supported ("any integer value from 1 to the maximum",
#   https://docs.cloud.google.com/run/docs/configuring/max-instances). It is already ingress internal and only Cloud
#   Tasks and Cloud Scheduler call it, so pausing both stops all of its traffic. (`--scaling=0` disables a service per
#   https://docs.cloud.google.com/run/docs/configuring/services/manual-scaling, but the gcloud reference says "a
#   positive integer"; not used.)
# - api and payments: --ingress=internal rejects public requests ("All other requests are rejected.",
#   https://docs.cloud.google.com/sdk/gcloud/reference/run/services/update). LemonSqueezy webhooks then fail and are
#   retried by LemonSqueezy for a limited time (unverified); while checkout is live, KEEP_PAYMENTS_PUBLIC=1 keeps the
#   ingress up (its events wait in the paused payments queue).
#
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] [KEEP_PAYMENTS_PUBLIC=1] [PAUSE=0|1] \
#   ./99-kill-switch.sh stop|resume
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params
ACTION="${1:-}"
case "$ACTION" in stop|resume) ;; *) die "usage: $0 stop|resume" ;; esac

PUBLIC_SERVICES="$(svc_api)"
if [ "${KEEP_PAYMENTS_PUBLIC:-0}" != "1" ]; then PUBLIC_SERVICES="$PUBLIC_SERVICES $(svc_payments)"; fi
FAILED=0
set_ingress() {  # set_ingress SERVICE internal|all; a failure is reported and the remaining steps still run
  run gcloud run services update "$1" --project="$PROJECT_ID" --region="$REGION" --ingress="$2" --quiet \
    || { echo "# FAILED: ingress $2 on $1" >&2; FAILED=1; }
}

if [ "$ACTION" = "stop" ]; then
  banner "Kill switch: STOP"
  if [ "$DRY_RUN" = "0" ]; then
    # The state before stop, for the record: resume restores the deploy defaults, not this snapshot.
    echo "# before stop:"
    for svc in $PUBLIC_SERVICES; do
      echo "#   $svc ingress=$(gcloud run services describe "$svc" --project="$PROJECT_ID" --region="$REGION" \
        --format='value(metadata.annotations."run.googleapis.com/ingress")' 2>/dev/null || echo unknown)"
    done
    for j in "$(sched_sweep)" "$(sched_cleanup)"; do
      echo "#   $j state=$(gcloud scheduler jobs describe "$j" --project="$PROJECT_ID" --location="$REGION" \
        --format='value(state)' 2>/dev/null || echo unknown)"
    done
  fi
  # Public ingress first: it is the cut that matters most, so nothing that can fail runs before it.
  for svc in $PUBLIC_SERVICES; do set_ingress "$svc" internal; done
  for q in "$(queue_jobs)" "$(queue_payments)"; do set_paused queue "$q" pause; done
  for j in "$(sched_sweep)" "$(sched_cleanup)"; do set_paused scheduler "$j" pause; done
  echo "# Stopped (in-flight requests finish). Check: gcloud tasks queues describe <queue> --location=$REGION --format='value(state)'"
else
  # Restores the deploy defaults: ingress all on api and payments, queues running, schedules as 70-scheduler.sh
  # leaves them (prod running, staging paused unless PAUSE=0). A schedule paused by hand before stop is resumed in prod.
  if [ "$ENV" = "staging" ]; then PAUSE="${PAUSE:-1}"; else PAUSE="${PAUSE:-0}"; fi
  banner "Kill switch: RESUME (schedules paused=$PAUSE)"
  for svc in "$(svc_api)" "$(svc_payments)"; do set_ingress "$svc" all; done
  for q in "$(queue_jobs)" "$(queue_payments)"; do set_paused queue "$q" resume; done
  if [ "$PAUSE" = "0" ]; then
    for j in "$(sched_sweep)" "$(sched_cleanup)"; do set_paused scheduler "$j" resume; done
  else
    echo "# Scheduler jobs left paused (PAUSE=1); PAUSE=0 resumes them"
  fi
fi
exit "$FAILED"
