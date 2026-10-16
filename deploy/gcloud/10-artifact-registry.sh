#!/usr/bin/env bash
# 10 - Artifact Registry Docker repository (shared by staging and prod: one digest is built once and promoted) and
# its cleanup policy. Project-wide (no ENV).
# Cleanup policy format and precedence ("When an artifact matches the criteria for both a delete policy and a keep
# policy, the artifact is kept."): https://docs.cloud.google.com/artifact-registry/docs/repositories/cleanup-policy
# CI tags every push with the commit SHA and adds prod-<sha> on promotion (DEPLOY-PLAN.md "Image strategy"), so the
# image prod runs is never deleted; anything else older than 30 days goes, except the 5 most recent versions.
# Usage: PROJECT_ID=... REGION=... [DRY_RUN=0] [CLEANUP_DRY_RUN=0] ./10-artifact-registry.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_base_params
POLICY_FILE="$(cd "$(dirname "$0")" && pwd)/ar-cleanup-policy.json"
# The policy itself starts in Artifact Registry's own dry-run mode (logs what it would delete) until the owner has
# looked at the result; CLEANUP_DRY_RUN=0 switches it to deleting.
if [ "${CLEANUP_DRY_RUN:-1}" = "0" ]; then CLEANUP_MODE="--no-dry-run"; else CLEANUP_MODE="--dry-run"; fi

banner "Artifact Registry repository"
if ! exists gcloud artifacts repositories describe "$AR_REPO" --project="$PROJECT_ID" --location="$REGION"; then
  run gcloud artifacts repositories create "$AR_REPO" --project="$PROJECT_ID" --location="$REGION" \
    --repository-format=docker --description="imageopz images (api, payments-ingress)" \
    --labels="app=imageopz"
fi

banner "Cleanup policy ($CLEANUP_MODE)"
echo "# policy file: $POLICY_FILE"
run gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --project="$PROJECT_ID" --location="$REGION" \
  --policy="$POLICY_FILE" "$CLEANUP_MODE"
