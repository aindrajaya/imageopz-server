#!/usr/bin/env bash
# 60 - Deploy the three Cloud Run services for ENV from images pinned BY DIGEST. First deploy by the owner; after
# that GitHub Actions deploys with the same flags (DEPLOY-PLAN.md "Image strategy").
# Order: worker first (its URL is the Cloud Tasks target and OIDC audience), then api and payments.
# Prerequisites: 20, 30 (with secret versions added), 40, and the migrate job executed (50-jobs.sh).
# The public invoker binding for api/payments is NOT set here (deployer stays without setIamPolicy): 65-service-iam.sh.
#
# Usage:
#   PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] \
#   API_IMAGE=REGION-docker.pkg.dev/PROJECT/imageopz/api@sha256:... \
#   PAYMENTS_IMAGE=REGION-docker.pkg.dev/PROJECT/imageopz/payments-ingress@sha256:... \
#   UI_ORIGINS=https://ui.example,https://www.ui.example \
#   R2_ACCOUNT_ID=... R2_BUCKET_NAME=... R2_CUSTOM_DOMAIN=https://... R2_UPLOADS_BUCKET=... \
#   SUPABASE_URL=https://<ref>.supabase.co SUPABASE_ANON_KEY=<publishable key> \
#   LEMONSQUEEZY_STORE_ID=... LEMONSQUEEZY_VARIANT_ID=... [LEMONSQUEEZY_REDIRECT_URL=...] \
#   ./60-deploy-services.sh
# All values above are non-secret configuration; see deploy/gcloud/env.example.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params
for v in API_IMAGE PAYMENTS_IMAGE UI_ORIGINS R2_ACCOUNT_ID R2_BUCKET_NAME R2_CUSTOM_DOMAIN R2_UPLOADS_BUCKET \
         SUPABASE_URL SUPABASE_ANON_KEY LEMONSQUEEZY_STORE_ID LEMONSQUEEZY_VARIANT_ID; do
  require_var "$v" "see deploy/gcloud/env.example"
done
case "$API_IMAGE" in *@sha256:*) ;; *) die "API_IMAGE must be pinned by digest (...@sha256:...)" ;; esac
case "$PAYMENTS_IMAGE" in *@sha256:*) ;; *) die "PAYMENTS_IMAGE must be pinned by digest (...@sha256:...)" ;; esac

if [ "$ENV" = "prod" ]; then APP_ENVIRONMENT=production; LS_TEST_MODE=false; else APP_ENVIRONMENT=staging; LS_TEST_MODE=true; fi
WORKER_URL="$(run_url "$(svc_worker)")"
INVOKER_EMAIL="$(sa_email tasks-invoker)"
TMPDIR_ENV="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_ENV"' EXIT

# YAML env file: --env-vars-file replaces all env vars on the revision (declarative), and values with commas
# (CORS_ALLOW_ORIGINS) need no delimiter escaping. Values are single-quoted YAML strings.
yaml_kv() { printf "%s: '%s'\n" "$1" "$(printf '%s' "$2" | sed "s/'/''/g")"; }
show_env_file() {
  echo "# --- $1 ---"
  cat "$1"
}

# --- worker: /internal/* only (jobs and payment events), private, internal ingress ---------------------------------
WORKER_ENV="$TMPDIR_ENV/worker.yaml"
{
  yaml_kv ENVIRONMENT "$APP_ENVIRONMENT"
  yaml_kv DEBUG false
  yaml_kv SERVICE_ROLE worker            # T-008b: mount /internal/* and /health only
  yaml_kv DB_POOL_SIZE 2
  yaml_kv INTERNAL_AUTH_MODE oidc
  yaml_kv INTERNAL_AUTH_AUDIENCE "$WORKER_URL"
  yaml_kv INTERNAL_AUTH_ALLOWED_EMAILS "$INVOKER_EMAIL"
  yaml_kv JOB_LEASE_SECONDS 1800
  yaml_kv R2_ACCOUNT_ID "$R2_ACCOUNT_ID"
  yaml_kv R2_BUCKET_NAME "$R2_BUCKET_NAME"
  yaml_kv R2_CUSTOM_DOMAIN "$R2_CUSTOM_DOMAIN"
  yaml_kv R2_UPLOADS_BUCKET "$R2_UPLOADS_BUCKET"
} > "$WORKER_ENV"
show_env_file "$WORKER_ENV"

banner "Deploy $(svc_worker)"
# --ingress=internal: Cloud Tasks in the same project counts as internal when it calls the run.app URL
#   (https://docs.cloud.google.com/run/docs/securing/ingress).
# --concurrency=1: one image job per instance (memory); --timeout=1800 >= Cloud Tasks dispatch deadline (ADR-005).
# --cpu/--memory: 50 MB / ~179 MP worst case; measure in staging before prod (DEPLOY-PLAN.md open question).
# --no-allow-unauthenticated: only principals with roles/run.invoker (the tasks-invoker SA) get through.
run gcloud run deploy "$(svc_worker)" --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" \
  --service-account="$(sa_email worker)" \
  --ingress=internal --no-allow-unauthenticated \
  --concurrency=1 --min-instances=0 --max-instances="$WORKER_MAX" \
  --cpu=2 --memory=4Gi --timeout=1800 --cpu-boost --execution-environment=gen2 \
  --env-vars-file="$WORKER_ENV" \
  --set-secrets="DATABASE_URL=$(secret_name database-url):latest,SECRET_KEY=$(secret_name secret-key):latest,R2_ACCESS_KEY_ID=$(secret_name r2-outputs-access-key-id):latest,R2_SECRET_ACCESS_KEY=$(secret_name r2-outputs-secret-access-key):latest,R2_UPLOADS_ACCESS_KEY_ID=$(secret_name r2-uploads-access-key-id):latest,R2_UPLOADS_SECRET_ACCESS_KEY=$(secret_name r2-uploads-secret-access-key):latest" \
  --labels="app=imageopz,env=${ENV},component=worker"

# --- api: public routes, no /internal/* (T-008b), public ingress -----------------------------------------------------
API_ENV="$TMPDIR_ENV/api.yaml"
{
  yaml_kv ENVIRONMENT "$APP_ENVIRONMENT"
  yaml_kv DEBUG false
  yaml_kv SERVICE_ROLE api               # T-008b: do not mount /internal/*; until then INTERNAL_AUTH_MODE unset = 401
  yaml_kv EXPOSE_API_DOCS "$([ "$ENV" = prod ] && echo false || echo true)"   # A1, T-008b
  yaml_kv CORS_ALLOW_ORIGINS "$UI_ORIGINS"
  yaml_kv CORS_ALLOW_CREDENTIALS true
  yaml_kv DB_POOL_SIZE 2
  yaml_kv JOB_ENQUEUE_MODE cloudtasks
  yaml_kv JOB_TASKS_QUEUE "$(queue_path "$(queue_jobs)")"
  yaml_kv JOB_TASKS_TARGET_URL "$WORKER_URL"
  yaml_kv JOB_TASKS_SA_EMAIL "$INVOKER_EMAIL"
  yaml_kv JOB_TASKS_AUDIENCE "$WORKER_URL"
  yaml_kv R2_ACCOUNT_ID "$R2_ACCOUNT_ID"
  yaml_kv R2_UPLOADS_BUCKET "$R2_UPLOADS_BUCKET"
  yaml_kv SUPABASE_JWKS_URL "${SUPABASE_URL%/}/auth/v1/.well-known/jwks.json"
  yaml_kv SUPABASE_JWT_ISSUER "${SUPABASE_URL%/}/auth/v1"
  yaml_kv SUPABASE_JWT_AUDIENCE authenticated
  yaml_kv SUPABASE_AUTH_URL "${SUPABASE_URL%/}/auth/v1"
  yaml_kv SUPABASE_ANON_KEY "$SUPABASE_ANON_KEY"
  yaml_kv LEMONSQUEEZY_STORE_ID "$LEMONSQUEEZY_STORE_ID"
  yaml_kv LEMONSQUEEZY_VARIANT_ID "$LEMONSQUEEZY_VARIANT_ID"
  yaml_kv LEMONSQUEEZY_REDIRECT_URL "${LEMONSQUEEZY_REDIRECT_URL:-}"
  yaml_kv LEMONSQUEEZY_TEST_MODE "$LS_TEST_MODE"
} > "$API_ENV"
show_env_file "$API_ENV"

banner "Deploy $(svc_api)"
# --concurrency=20 with DB_POOL_SIZE=2: requests beyond 2 concurrent DB sessions wait for the pool (30 s), so keep
#   concurrency modest; --max-instances caps cost and pooler connections (G1).
run gcloud run deploy "$(svc_api)" --project="$PROJECT_ID" --region="$REGION" \
  --image="$API_IMAGE" \
  --service-account="$(sa_email api)" \
  --ingress=all \
  --concurrency=20 --min-instances=0 --max-instances="$API_MAX" \
  --cpu=1 --memory=512Mi --timeout=60 --cpu-boost \
  --env-vars-file="$API_ENV" \
  --set-secrets="DATABASE_URL=$(secret_name database-url):latest,SECRET_KEY=$(secret_name secret-key):latest,R2_UPLOADS_ACCESS_KEY_ID=$(secret_name r2-uploads-access-key-id):latest,R2_UPLOADS_SECRET_ACCESS_KEY=$(secret_name r2-uploads-secret-access-key):latest,LEMONSQUEEZY_SECRET=$(secret_name lemonsqueezy-signing-secret):latest,LEMONSQUEEZY_API_KEY=$(secret_name lemonsqueezy-api-key):latest" \
  --labels="app=imageopz,env=${ENV},component=api"

# --- payments: LemonSqueezy webhook ingress (Go), public ingress ----------------------------------------------------
PAYMENTS_ENV="$TMPDIR_ENV/payments.yaml"
{
  yaml_kv ENQUEUE_MODE cloudtasks
  yaml_kv TASKS_QUEUE "$(queue_path "$(queue_payments)")"
  yaml_kv TASKS_TARGET_URL "$WORKER_URL"
  yaml_kv TASKS_SA_EMAIL "$INVOKER_EMAIL"
  yaml_kv TASKS_AUDIENCE "$WORKER_URL"
} > "$PAYMENTS_ENV"
show_env_file "$PAYMENTS_ENV"

banner "Deploy $(svc_payments)"
# --concurrency=8 matches the Go pool (pgxpool MaxConns = 8, services/payments-go/internal/httpapi/pgstore.go).
run gcloud run deploy "$(svc_payments)" --project="$PROJECT_ID" --region="$REGION" \
  --image="$PAYMENTS_IMAGE" \
  --service-account="$(sa_email payments)" \
  --ingress=all \
  --concurrency=8 --min-instances=0 --max-instances="$PAYMENTS_MAX" \
  --cpu=1 --memory=512Mi --timeout=30 \
  --env-vars-file="$PAYMENTS_ENV" \
  --set-secrets="DATABASE_URL=$(secret_name database-url):latest,LEMONSQUEEZY_SIGNING_SECRET=$(secret_name lemonsqueezy-signing-secret):latest" \
  --labels="app=imageopz,env=${ENV},component=payments"
