#!/usr/bin/env bash
# ci-rollout - Image rollout for ENV, run by .github/workflows/deploy.yml (DRY_RUN=0 there) as iz-deployer-<env>.
# Changes images only: `update --image` keeps every other setting of the job/service, which 50 and 60 own (flags,
# env, secrets) and 65 owns (IAM). The job and services must exist (first deploy by the owner: 50, 60, 65).
# Order: migrate job (wait; a failure stops the rollout), then api, worker, payments; then a smoke test.
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] \
#   API_IMAGE=...api@sha256:... PAYMENTS_IMAGE=...payments-ingress@sha256:... ./ci-rollout.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params
require_var API_IMAGE "Python image by digest"
require_var PAYMENTS_IMAGE "Go image by digest"
case "$API_IMAGE" in *@sha256:*) ;; *) die "API_IMAGE must be pinned by digest (...@sha256:...)" ;; esac
case "$PAYMENTS_IMAGE" in *@sha256:*) ;; *) die "PAYMENTS_IMAGE must be pinned by digest (...@sha256:...)" ;; esac

banner "Migrate $(job_migrate)"
# Expand/contract: the previous revisions keep serving during the migration (DEPLOY-PLAN.md section 6).
run gcloud run jobs update "$(job_migrate)" --project="$PROJECT_ID" --region="$REGION" --image="$API_IMAGE" --quiet
run gcloud run jobs execute "$(job_migrate)" --project="$PROJECT_ID" --region="$REGION" --wait --quiet

banner "Services"
run gcloud run services update "$(svc_api)" --project="$PROJECT_ID" --region="$REGION" --image="$API_IMAGE" --quiet
run gcloud run services update "$(svc_worker)" --project="$PROJECT_ID" --region="$REGION" --image="$API_IMAGE" --quiet
run gcloud run services update "$(svc_payments)" --project="$PROJECT_ID" --region="$REGION" --image="$PAYMENTS_IMAGE" --quiet

banner "Smoke test (public services; the worker is internal-only)"
smoke() {  # smoke SERVICE PATH...
  local svc="$1" url path; shift
  if [ "$DRY_RUN" != "0" ]; then
    url="$(run_url "$svc")"
  else
    url="$(gcloud run services describe "$svc" --project="$PROJECT_ID" --region="$REGION" --format='value(status.url)')"
  fi
  for path in "$@"; do
    run curl -fsS --retry 5 --retry-delay 3 --retry-all-errors -o /dev/null "${url}${path}"
  done
}
smoke "$(svc_api)" /health /ready /version
smoke "$(svc_payments)" /health /ready
