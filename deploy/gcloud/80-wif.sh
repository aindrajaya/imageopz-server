#!/usr/bin/env bash
# 80 - CI deploy identity for ENV via Workload Identity Federation (GitHub Actions, no keys).
# - Pool + provider (project-wide, idempotent): only tokens from repository aindrajaya/imageopz-server (matched by
#   immutable numeric ids) on refs/heads/main from the deploy workflow are accepted (attribute condition).
#   https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines
#   ("use the numeric *_id fields instead, which are unique and can't be reused").
# - One deployer service account per environment, impersonated (Cloud Run does not support direct resource access
#   for federated principals: https://docs.cloud.google.com/iam/docs/federated-identity-supported-services).
#   prod's deployer is only reachable from a workflow job bound to the GitHub Environment "production" (owner
#   approval gate in the workflow, T-008b).
# - Deployer roles are resource-scoped: artifactregistry.writer on the repo; run.developer on the ENV's services and
#   migrate job, serviceAccountUser on the ENV's runtime identities only.
#
# Two stages (T-008c1, bootstrap: the first image is built by CI before any service exists):
#   WIF_STAGE=registry  pool, provider, deployer, its workloadIdentityUser binding, artifactregistry.writer. Enough
#                       for `deploy.yml` with build_only=true (push images); nothing Cloud Run is touched.
#   WIF_STAGE=all       (default) registry, plus run.developer on the 3 services and the migrate job and actAs on the
#                       runtime identities. The services and the job must exist (50, 60); checked before any change.
#
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] [WIF_STAGE=registry|all] \
#   GITHUB_REPOSITORY_ID=<numeric> GITHUB_OWNER_ID=<numeric> ./80-wif.sh
#   (ids: gh api repos/aindrajaya/imageopz-server --jq '.id, .owner.id')
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params
require_var GITHUB_REPOSITORY_ID "numeric repository id"
require_var GITHUB_OWNER_ID "numeric owner id"
WIF_STAGE="${WIF_STAGE:-all}"
case "$WIF_STAGE" in registry|all) ;; *) die "WIF_STAGE must be registry or all" ;; esac

POOL="github"
PROVIDER="imageopz-server"
PNUM="$(project_number)"
POOL_RESOURCE="projects/${PNUM}/locations/global/workloadIdentityPools/${POOL}"
DEPLOYER_ID="iz-deployer-${ENV}"
DEPLOYER="${DEPLOYER_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

# Fail before any change: run.developer is bound per resource name, so a missing service or job would stop the run
# half-way (set -e) with the registry part applied and the rest not.
if [ "$WIF_STAGE" = "all" ]; then
  banner "Preflight: ${ENV} services and migrate job exist (WIF_STAGE=all)"
  missing=""
  for svc in "$(svc_api)" "$(svc_worker)" "$(svc_payments)"; do
    exists gcloud run services describe "$svc" --project="$PROJECT_ID" --region="$REGION" || missing="$missing service/$svc"
  done
  exists gcloud run jobs describe "$(job_migrate)" --project="$PROJECT_ID" --region="$REGION" \
    || missing="$missing job/$(job_migrate)"
  if [ "$DRY_RUN" = "0" ] && [ -n "$missing" ]; then
    die "WIF_STAGE=all needs the ${ENV} services and migrate job, missing:${missing}. Deploy them first (50-jobs.sh, 60-deploy-services.sh), or run WIF_STAGE=registry for the CI image push only."
  fi
fi

banner "Workload Identity pool and GitHub provider"
if ! exists gcloud iam workload-identity-pools describe "$POOL" --project="$PROJECT_ID" --location=global; then
  run gcloud iam workload-identity-pools create "$POOL" --project="$PROJECT_ID" --location=global \
    --display-name="GitHub Actions"
fi
# repo_env = "<repository_id>:<environment>" so the prod principalSet cannot match another repo or provider in the pool.
MAPPING="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref,attribute.repo_env=assertion.repository_id + ':' + (has(assertion.environment) ? assertion.environment : 'none')"
# workflow_ref pins the one deploy workflow (T-008b creates it under this name): a later pull_request_target or
# workflow_run workflow also runs with ref == main and must not be able to mint deployer tokens. A workflow_dispatch
# run of deploy.yml on main carries the same ref and workflow_ref, so build_only runs match too.
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

banner "Deployer roles: Artifact Registry"
# staging's deployer pushes the images (build job); prod's adds the prod-<sha> tags.
run gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --project="$PROJECT_ID" --location="$REGION" \
  --member="serviceAccount:${DEPLOYER}" --role=roles/artifactregistry.writer

if [ "$WIF_STAGE" = "all" ]; then
  banner "Deployer roles: Cloud Run (resource-scoped)"
  for svc in "$(svc_api)" "$(svc_worker)" "$(svc_payments)"; do
    run gcloud run services add-iam-policy-binding "$svc" --project="$PROJECT_ID" --region="$REGION" \
      --member="serviceAccount:${DEPLOYER}" --role=roles/run.developer
  done
  # run.developer on the jobs also covers executing the migrate job (run.jobs.run). It ALSO includes
  # run.jobs.runWithOverrides and delete (accepted risk, DEPLOY-PLAN.md section 4; hardening: custom role, T-008b).
  run gcloud run jobs add-iam-policy-binding "$(job_migrate)" --project="$PROJECT_ID" --region="$REGION" \
    --member="serviceAccount:${DEPLOYER}" --role=roles/run.developer
  # actAs: deploying a revision that runs as another identity needs Service Account User on that identity
  # (https://docs.cloud.google.com/run/docs/deploying).
  for role in api worker payments migrate; do
    run gcloud iam service-accounts add-iam-policy-binding "$(sa_email "$role")" --project="$PROJECT_ID" \
      --member="serviceAccount:${DEPLOYER}" --role=roles/iam.serviceAccountUser
  done
else
  echo
  echo "# WIF_STAGE=registry: Cloud Run roles not granted. After 50 and 60 have created the ${ENV} job and services,"
  echo "# run this script again with WIF_STAGE=all (it re-applies the registry part idempotently)."
fi

echo
echo "# GitHub Actions (google-github-actions/auth) inputs for ${ENV}:"
echo "#   workload_identity_provider: ${POOL_RESOURCE}/providers/${PROVIDER}"
echo "#   service_account: ${DEPLOYER}"
