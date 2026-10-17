#!/usr/bin/env bash
# 40 - Cloud Tasks queues for ENV (jobs, payments), their enqueuer bindings, and actAs on the invoker identity.
# Create when absent, otherwise update to the same config (converges). Requires 20-service-accounts.sh.
# Flags: https://docs.cloud.google.com/sdk/gcloud/reference/tasks/queues/create
# Retry semantics: https://docs.cloud.google.com/tasks/docs/reference/rest/v2/RetryConfig
#   "Cloud Tasks stops retrying only when maxAttempts and maxRetryDuration are both satisfied".
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] ./40-queues.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params

# jobs queue (ADR-005, J3): a delivery during a live lease answers 503, so retries must outlast
# JOB_LEASE_SECONDS=1800. Backoff 30,60,120,240,480 then 600 s steps: the 7th retry lands ~2130 s after the
# first failure (> 1800 s); retries stop once BOTH 15 attempts and 7200 s have passed (~17 attempts). Bounded.
# max-concurrent-dispatches 1 (Q5): the worker runs max-instances 1 at concurrency 2, so one slot stays free for a
# payment event or a maintenance call.
JOBS_FLAGS="--max-attempts=15 --max-retry-duration=7200s --min-backoff=30s --max-backoff=600s --max-doublings=5
  --max-dispatches-per-second=2 --max-concurrent-dispatches=${JOBS_QUEUE_CONCURRENCY} --log-sampling-ratio=1.0"

# payments queue (ADR-001, Q9): the ingress has already answered 2xx to LemonSqueezy, so this queue is the only retry
# path for an activation. Backoff 10 s doubling to a 600 s cap (bounded), retries for 7 days: they stop once BOTH 50
# attempts and 604800 s have passed (~1,000 attempts at the cap; the duration governs). Exhaustion needs a manual
# replay; old tasks are caught earlier by the alert in 90-observability.sh (DEPLOY-PLAN.md "Observability").
PAYMENTS_FLAGS="--max-attempts=50 --max-retry-duration=604800s --min-backoff=10s --max-backoff=600s --max-doublings=6
  --max-dispatches-per-second=5 --max-concurrent-dispatches=${PAYMENTS_QUEUE_CONCURRENCY} --log-sampling-ratio=1.0"

ensure_queue() {
  local queue="$1" flags="$2"
  # shellcheck disable=SC2086  # flags are a fixed, space-separated list defined above
  if exists gcloud tasks queues describe "$queue" --project="$PROJECT_ID" --location="$REGION"; then
    run gcloud tasks queues update "$queue" --project="$PROJECT_ID" --location="$REGION" $flags
  else
    run gcloud tasks queues create "$queue" --project="$PROJECT_ID" --location="$REGION" $flags
  fi
}

banner "Queues"
ensure_queue "$(queue_jobs)" "$JOBS_FLAGS"
ensure_queue "$(queue_payments)" "$PAYMENTS_FLAGS"

banner "Enqueuer bindings (queue-scoped)"
# api: /uploads/complete; worker: the scheduled sweep re-enqueues stale PENDING jobs (Q1).
for role in api worker; do
  run gcloud tasks queues add-iam-policy-binding "$(queue_jobs)" --project="$PROJECT_ID" --location="$REGION" \
    --member="serviceAccount:$(sa_email "$role")" --role=roles/cloudtasks.enqueuer
done
run gcloud tasks queues add-iam-policy-binding "$(queue_payments)" --project="$PROJECT_ID" --location="$REGION" \
  --member="serviceAccount:$(sa_email payments)" --role=roles/cloudtasks.enqueuer

banner "actAs on the OIDC identity (tasks-invoker), scoped to that one service account"
# "The caller must have iam.serviceAccounts.actAs permission for the service account."
# https://docs.cloud.google.com/tasks/docs/reference/rest/v2/OidcToken
for role in api worker payments; do
  run gcloud iam service-accounts add-iam-policy-binding "$(sa_email tasks-invoker)" --project="$PROJECT_ID" \
    --member="serviceAccount:$(sa_email "$role")" --role=roles/iam.serviceAccountUser
done
# The Cloud Tasks service agent mints the OIDC token. Its default role (cloudtasks.serviceAgent) carries
# getOpenIdToken, but https://docs.cloud.google.com/tasks/docs/creating-http-target-tasks also asks for Service
# Account User on the OIDC service account; granted here, scoped to the invoker SA only (DEPLOY-PLAN.md notes the
# doc conflict).
TASKS_AGENT="service-$(project_number)@gcp-sa-cloudtasks.iam.gserviceaccount.com"
run gcloud iam service-accounts add-iam-policy-binding "$(sa_email tasks-invoker)" --project="$PROJECT_ID" \
  --member="serviceAccount:${TASKS_AGENT}" --role=roles/iam.serviceAccountUser
