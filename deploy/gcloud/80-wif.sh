#!/usr/bin/env bash
# 80 - CI deploy identity for ENV via Workload Identity Federation (GitHub Actions, no keys).
# - Pool + provider (project-wide, idempotent): only tokens from repository aindrajaya/imageopz-server (matched by
#   immutable numeric ids) on refs/heads/main are accepted (attribute condition).
#   https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines
#   ("use the numeric *_id fields instead, which are unique and can't be reused").
# - One deployer service account per environment, impersonated (Cloud Run does not support direct resource access
#   for federated principals: https://docs.cloud.google.com/iam/docs/federated-identity-supported-services).
#   prod's deployer is only reachable from a workflow job bound to the GitHub Environment "production" (owner
#   approval gate in the workflow, T-008b).
# - Deployer roles are resource-scoped: run.developer on the ENV's services and jobs (they must exist: run 50 and 60
#   first), artifactregistry.writer on the repo, serviceAccountUser on the ENV's runtime identities only.
#
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] \
#   GITHUB_REPOSITORY_ID=<numeric> GITHUB_OWNER_ID=<numeric> ./80-wif.sh
#   (ids: gh api repos/aindrajaya/imageopz-server --jq '.id, .owner.id')
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params
require_var GITHUB_REPOSITORY_ID "numeric repository id"
require_var GITHUB_OWNER_ID "numeric owner id"

POOL="github"
PROVIDER="imageopz-server"
PNUM="$(project_number)"
POOL_RESOURCE="projects/${PNUM}/locations/global/workloadIdentityPools/${POOL}"
DEPLOYER_ID="iz-deployer-${ENV}"
DEPLOYER="${DEPLOYER_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

banner "Workload Identity pool and GitHub provider"
if ! exists gcloud iam workload-identity-pools describe "$POOL" --project="$PROJECT_ID" --location=global; then
  run gcloud iam workload-identity-pools create "$POOL" --project="$PROJECT_ID" --location=global \
    --display-name="GitHub Actions"
fi
# repo_env = "<repository_id>:<environment>" so the prod principalSet cannot match another repo or provider in the pool.
MAPPING="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref,attribute.repo_env=assertion.repository_id + ':' + (has(assertion.environment) ? assertion.environment : 'none')"
# workflow_ref pins the one deploy workflow (T-008b creates it under this name): a later pull_request_target or
# workflow_run workflow also runs with ref == main and must not be able to mint deployer tokens.
DEPLOY_WORKFLOW="${DEPLOY_WORKFLOW:-aindrajaya/imageopz-server/.github/workflows/deploy.yml@refs/heads/main}"
CONDITION="assertion.repository_id=='${GITHUB_REPOSITORY_ID}' && assertion.repository_owner_id=='${GITHUB_OWNER_ID}' && assertion.ref=='refs/heads/main' && assertion.workflow_ref=='${DEPLOY_WORKFLOW}'"
if exists gcloud iam workload-identity-pools providers describe "$PROVIDER" --project="$PROJECT_ID" \
    --location=global --workload-identity-pool="$POOL"; then
  run gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" --project="$PROJECT_ID" \
    --location=global --workload-identity-pool="$POOL" \
    --attribute-mapping="$MAPPING" --attribute-condition="$CONDITION"
else
  run gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --project="$PROJECT_ID" \
    --location=global --workload-identity-pool="$POOL" --display-name="imageopz-server main" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="$MAPPING" --attribute-condition="$CONDITION"
fi

banner "Deployer identity $DEPLOYER_ID"
if ! exists gcloud iam service-accounts describe "$DEPLOYER" --project="$PROJECT_ID"; then
  run gcloud iam service-accounts create "$DEPLOYER_ID" --project="$PROJECT_ID" \
    --display-name="imageopz ${ENV} CI deployer" --description="GitHub Actions via WIF; no keys"
fi
# assertion.environment is present only for jobs bound to a GitHub Environment, hence the has() guard (validate on the
# first CI run with both an environment job and a plain job).
# Who may impersonate: staging = any main-branch job of the repo (provider condition);
# prod = additionally only jobs running in the GitHub Environment "production".
if [ "$ENV" = "prod" ]; then
  PRINCIPAL="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repo_env/${GITHUB_REPOSITORY_ID}:production"
else
  PRINCIPAL="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repository_id/${GITHUB_REPOSITORY_ID}"
fi
run gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --project="$PROJECT_ID" \
  --member="$PRINCIPAL" --role=roles/iam.workloadIdentityUser

banner "Deployer roles (resource-scoped)"
run gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --project="$PROJECT_ID" --location="$REGION" \
  --member="serviceAccount:${DEPLOYER}" --role=roles/artifactregistry.writer
for svc in "$(svc_api)" "$(svc_worker)" "$(svc_payments)"; do
  run gcloud run services add-iam-policy-binding "$svc" --project="$PROJECT_ID" --region="$REGION" \
    --member="serviceAccount:${DEPLOYER}" --role=roles/run.developer
done
# run.developer on the jobs also covers executing the migrate job (run.jobs.run). It ALSO includes
# run.jobs.runWithOverrides and delete (accepted risk, DEPLOY-PLAN.md section 4; hardening: custom role, T-008b).
for job in "$(job_migrate)" "$(job_sweep)" "$(job_cleanup)"; do
  run gcloud run jobs add-iam-policy-binding "$job" --project="$PROJECT_ID" --region="$REGION" \
    --member="serviceAccount:${DEPLOYER}" --role=roles/run.developer
done
# actAs: deploying a revision that runs as another identity needs Service Account User on that identity
# (https://docs.cloud.google.com/run/docs/deploying).
for role in api worker payments migrate sweep cleanup; do
  run gcloud iam service-accounts add-iam-policy-binding "$(sa_email "$role")" --project="$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER}" --role=roles/iam.serviceAccountUser
done

echo
echo "# GitHub Actions (google-github-actions/auth) inputs for ${ENV}:"
echo "#   workload_identity_provider: ${POOL_RESOURCE}/providers/${PROVIDER}"
echo "#   service_account: ${DEPLOYER}"
