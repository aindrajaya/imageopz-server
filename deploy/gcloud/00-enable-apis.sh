#!/usr/bin/env bash
# 00 - Enable the APIs used by the plan. Project-wide (no ENV). `services enable` is idempotent.
# Usage: PROJECT_ID=imageopz-prod-4821 REGION=asia-northeast1 [DRY_RUN=0] ./00-enable-apis.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_base_params

banner "Enable APIs"
# iamcredentials + sts: Workload Identity Federation for GitHub Actions (80-wif.sh).
# billingbudgets: the project budget (90-observability.sh). cloudresourcemanager: project IAM reads/writes.
run gcloud services enable --project="$PROJECT_ID" \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudtasks.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  billingbudgets.googleapis.com
