#!/usr/bin/env bash
# 65 - Invoker bindings on the Cloud Run services of ENV (service-scoped, never project-level run.invoker).
# Run by the owner after the first 60-deploy-services.sh; CI deploys never change IAM.
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] ./65-service-iam.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params

banner "Service invoker bindings"
# worker: only the identities whose OIDC tokens Cloud Tasks (both queues) and Cloud Scheduler (maintenance, Q1) send.
# https://docs.cloud.google.com/run/docs/authenticating/service-to-service
for role in tasks-invoker scheduler; do
  run gcloud run services add-iam-policy-binding "$(svc_worker)" --project="$PROJECT_ID" --region="$REGION" \
    --member="serviceAccount:$(sa_email "$role")" --role=roles/run.invoker
done

# api and payments are public endpoints (browser and LemonSqueezy). allUsers invoker is the documented way to allow
# unauthenticated access; an org policy (domain restricted sharing) would block it - the project has no org (verify).
for svc in "$(svc_api)" "$(svc_payments)"; do
  run gcloud run services add-iam-policy-binding "$svc" --project="$PROJECT_ID" --region="$REGION" \
    --member=allUsers --role=roles/run.invoker
done
