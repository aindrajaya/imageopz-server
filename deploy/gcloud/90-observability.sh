#!/usr/bin/env bash
# 90 - Budget, one log-based metric and alert policies for ENV (DEPLOY-PLAN.md "Observability").
# Only free-tier-friendly signals: log-match alert policies (no metric needed), one user-defined counter (J5), and
# the built-in Cloud Tasks queue depth metric (jobs backlog, payments not draining). Alerting is not charged before 2027-09-01
# (https://cloud.google.com/products/observability/pricing).
# Budget: project-scoped, alerts only ("doesn't automatically cap ... spending":
# https://docs.cloud.google.com/billing/docs/how-to/budgets). Created once (ENV=prod run) with BILLING_ACCOUNT.
#
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] \
#   [NOTIFICATION_CHANNEL=projects/P/notificationChannels/ID]   (email channel created by the owner in the console)
#   [BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX] [BUDGET_AMOUNT=10USD] ./90-observability.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params
TMPDIR_POL="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_POL"' EXIT

WORKER="$(svc_worker)"; API="$(svc_api)"; PAYMENTS="$(svc_payments)"

# --- J5: retriable 5xx answered by the worker (503 = live lease, transient error, or a bug) ------------------------
METRIC="imageopz-worker-5xx-${ENV}"
METRIC_FILTER="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${WORKER}\" AND httpRequest.status>=500"
banner "Log-based metric $METRIC"
if exists gcloud logging metrics describe "$METRIC" --project="$PROJECT_ID"; then
  run gcloud logging metrics update "$METRIC" --project="$PROJECT_ID" --log-filter="$METRIC_FILTER"
else
  run gcloud logging metrics create "$METRIC" --project="$PROJECT_ID" \
    --description="Worker 5xx (J5): retries, live-lease 503s, unexpected errors" --log-filter="$METRIC_FILTER"
fi

if [ -z "${NOTIFICATION_CHANNEL:-}" ]; then
  echo "# NOTIFICATION_CHANNEL unset: skipping alert policies (create an email channel in the console first)"
else
  # Log-match policy: fires on the first matching entry, at most once per rate-limit period.
  # The app logs plain text (logging.basicConfig: "time - name - LEVEL - msg"); the severity Cloud Run assigns to such
  # lines is unverified, so filters also match " - ERROR - ". Verify each policy fires in staging (go-live item 17).
  log_policy() {  # log_policy FILE DISPLAY_NAME FILTER
    cat > "$1" <<YAML
displayName: "$2"
combiner: OR
conditions:
- displayName: "$2"
  conditionMatchedLog:
    filter: '$(printf '%s' "$3" | sed "s/'/''/g")'
alertStrategy:
  notificationRateLimit:
    period: 3600s
  autoClose: 86400s
YAML
  }
  log_policy "$TMPDIR_POL/payments-events.yaml" "imageopz ${ENV}: payment event failed at the API" \
    "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${WORKER}\" AND httpRequest.requestUrl=~\"/internal/payments/events\" AND httpRequest.status>=500"
  log_policy "$TMPDIR_POL/payments-ingress.yaml" "imageopz ${ENV}: payments ingress 5xx" \
    "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${PAYMENTS}\" AND httpRequest.status>=500"
  log_policy "$TMPDIR_POL/app-errors.yaml" "imageopz ${ENV}: application ERROR logs (api, worker)" \
    "resource.type=\"cloud_run_revision\" AND (resource.labels.service_name=\"${API}\" OR resource.labels.service_name=\"${WORKER}\") AND (severity>=ERROR OR textPayload=~\" - ERROR - \")"
  log_policy "$TMPDIR_POL/jobs.yaml" "imageopz ${ENV}: migrate job error" \
    "resource.type=\"cloud_run_job\" AND resource.labels.job_name=~\"^imageopz-.*-${ENV}\$\" AND (severity>=ERROR OR textPayload=~\" - ERROR - \")"

  # Metric threshold policies: J5 burst, and a jobs backlog that does not drain.
  cat > "$TMPDIR_POL/worker-5xx.yaml" <<YAML
displayName: "imageopz ${ENV}: worker 5xx burst (J5)"
combiner: OR
conditions:
- displayName: "worker 5xx > 30 in 1 h"
  conditionThreshold:
    filter: 'metric.type="logging.googleapis.com/user/${METRIC}" AND resource.type="cloud_run_revision"'
    aggregations:
    - alignmentPeriod: 3600s
      perSeriesAligner: ALIGN_SUM
      crossSeriesReducer: REDUCE_SUM
    comparison: COMPARISON_GT
    thresholdValue: 30
    duration: 0s
YAML
  cat > "$TMPDIR_POL/queue-depth.yaml" <<YAML
displayName: "imageopz ${ENV}: jobs queue backlog"
combiner: OR
conditions:
- displayName: "jobs queue depth > 10 for 15 min"
  conditionThreshold:
    filter: 'metric.type="cloudtasks.googleapis.com/queue/depth" AND resource.type="cloud_tasks_queue" AND resource.labels.queue_id="$(queue_jobs)"'
    aggregations:
    - alignmentPeriod: 300s
      perSeriesAligner: ALIGN_MAX
    comparison: COMPARISON_GT
    thresholdValue: 10
    duration: 900s
YAML

  # Old payment tasks (Q9): Cloud Tasks has no "oldest task age" metric (the cloudtasks metrics are api/request_count,
  # queue/depth, queue/task_attempt_count, queue/task_attempt_delays: https://docs.cloud.google.com/monitoring/api/metrics_gcp_c).
  # A healthy payments queue drains in seconds, so any task still queued after 1 h is retrying; alert long before the
  # 7-day retry limit. Google Cloud metrics are free ("Metric data from Google Cloud ... isn't chargeable":
  # https://docs.cloud.google.com/stackdriver/pricing). Manual check: README "Old payment tasks".
  cat > "$TMPDIR_POL/payments-queue-age.yaml" <<YAML
displayName: "imageopz ${ENV}: payments queue not draining"
combiner: OR
conditions:
- displayName: "payments queue depth > 0 for 1 h"
  conditionThreshold:
    filter: 'metric.type="cloudtasks.googleapis.com/queue/depth" AND resource.type="cloud_tasks_queue" AND resource.labels.queue_id="$(queue_payments)"'
    aggregations:
    - alignmentPeriod: 300s
      perSeriesAligner: ALIGN_MIN
    comparison: COMPARISON_GT
    thresholdValue: 0
    duration: 3600s
YAML

  banner "Alert policies (skipped when a policy with the same display name exists)"
  for f in "$TMPDIR_POL"/*.yaml; do
    echo "# --- $f"; cat "$f"
    name="$(sed -n 's/^displayName: "\(.*\)"$/\1/p' "$f")"
    if [ "$DRY_RUN" = "0" ] && [ -n "$(gcloud monitoring policies list --project="$PROJECT_ID" \
        --filter="displayName=\"${name}\"" --format='value(name)')" ]; then
      echo "# exists: ${name} (update it by id if the file changed)"
      continue
    fi
    run gcloud monitoring policies create --project="$PROJECT_ID" \
      --policy-from-file="$f" --notification-channels="$NOTIFICATION_CHANNEL"
  done
fi

if [ -n "${BILLING_ACCOUNT:-}" ]; then
  banner "Budget (project-scoped, alerts at 50/90/100 %)"
  BUDGET_NAME="imageopz ${PROJECT_ID}"
  if [ "$DRY_RUN" = "0" ] && [ -n "$(gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" \
      --filter="displayName=\"${BUDGET_NAME}\"" --format='value(name)')" ]; then
    echo "# exists: ${BUDGET_NAME}"
  else
    run gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" \
      --display-name="$BUDGET_NAME" --budget-amount="${BUDGET_AMOUNT:-10USD}" \
      --filter-projects="projects/${PROJECT_ID}" \
      --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
  fi
else
  echo "# BILLING_ACCOUNT unset: skipping the budget (create it once; budgets are per billing account)"
fi
