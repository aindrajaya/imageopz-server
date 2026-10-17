#!/usr/bin/env bash
# 99 - Kill switch for ENV (Q8): stop spend without deleting anything; `resume` reverses it. Owner-run.
#   stop:   pause both Cloud Tasks queues, pause both Scheduler jobs, set api and payments to ingress internal.
#   resume: resume both queues, set api and payments back to ingress all, resume the Scheduler jobs (prod; staging
#           stays paused like 70-scheduler.sh unless PAUSE=0).
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

if [ "$ACTION" = "stop" ]; then
  banner "Kill switch: STOP"
  for q in "$(queue_jobs)" "$(queue_payments)"; do
    run gcloud tasks queues pause "$q" --project="$PROJECT_ID" --location="$REGION"
  done
  for j in "$(sched_sweep)" "$(sched_cleanup)"; do
    run gcloud scheduler jobs pause "$j" --project="$PROJECT_ID" --location="$REGION"
  done
  for svc in $PUBLIC_SERVICES; do
    run gcloud run services update "$svc" --project="$PROJECT_ID" --region="$REGION" --ingress=internal --quiet
  done
  echo "# Stopped. Check: gcloud tasks queues describe <queue> --location=$REGION --format='value(state)' = PAUSED"
else
  if [ "$ENV" = "staging" ]; then PAUSE="${PAUSE:-1}"; else PAUSE="${PAUSE:-0}"; fi
  banner "Kill switch: RESUME (schedules paused=$PAUSE)"
  for svc in $PUBLIC_SERVICES; do
    run gcloud run services update "$svc" --project="$PROJECT_ID" --region="$REGION" --ingress=all --quiet
  done
  for q in "$(queue_jobs)" "$(queue_payments)"; do
    run gcloud tasks queues resume "$q" --project="$PROJECT_ID" --location="$REGION"
  done
  if [ "$PAUSE" = "0" ]; then
    for j in "$(sched_sweep)" "$(sched_cleanup)"; do
      run gcloud scheduler jobs resume "$j" --project="$PROJECT_ID" --location="$REGION"
    done
  else
    echo "# Scheduler jobs left paused (PAUSE=1); PAUSE=0 resumes them"
  fi
fi
