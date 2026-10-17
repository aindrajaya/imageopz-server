#!/usr/bin/env bash
# 30 - Secret Manager containers for ENV and per-secret accessor bindings. Creates NO versions: the owner adds each
# value afterwards, never on the command line or in shell history:
#   gcloud secrets versions add imageopz-<name>-<env> --project=PROJECT_ID --data-file=-   (paste, then Ctrl-D)
# Mapping secret -> env var -> consumers: DEPLOY-PLAN.md "Secrets". Requires 20-service-accounts.sh.
# database-url / database-url-session values: bare postgresql://postgres.<ref>:...@<pooler-host>:6543|5432/postgres
# (never postgresql+psycopg://: the Go ingress shares database-url and pgx rejects that scheme; Python maps the bare form).
# Usage: PROJECT_ID=... REGION=... ENV=staging|prod [DRY_RUN=0] ./30-secrets.sh
set -euo pipefail
. "$(dirname "$0")/lib.sh"
require_env_params

# name:consumer roles (space-separated). Keep in sync with DEPLOY-PLAN.md "Secrets".
# secret-key is needed by every Python process that runs with ENVIRONMENT=production (config validator), so the worker
# and migrate read it too until the SECRET_KEY scope change (DEPLOY-PLAN.md section 5). Staging uses the same consumers.
# The worker runs the scheduled sweep (uploads bucket) and cleanup (outputs bucket) since Q1.
SECRETS="
database-url:api,worker,payments
database-url-session:migrate
secret-key:api,worker,migrate
r2-outputs-access-key-id:worker
r2-outputs-secret-access-key:worker
r2-uploads-access-key-id:api,worker
r2-uploads-secret-access-key:api,worker
lemonsqueezy-signing-secret:api,payments
lemonsqueezy-api-key:api
"

banner "Secrets (containers only, no values)"
for entry in $SECRETS; do
  name="${entry%%:*}"
  consumers="${entry#*:}"
  secret="$(secret_name "$name")"
  if ! exists gcloud secrets describe "$secret" --project="$PROJECT_ID"; then
    # One replica in the deployment region (user-managed replication): data stays in Tokyo, one location billed.
    run gcloud secrets create "$secret" --project="$PROJECT_ID" \
      --replication-policy=user-managed --locations="$REGION" \
      --labels="app=imageopz,env=${ENV}"
  fi
  # Accessor on THIS secret only, for each consuming identity (no project-level secretAccessor).
  for role in $(echo "$consumers" | tr ',' ' '); do
    run gcloud secrets add-iam-policy-binding "$secret" --project="$PROJECT_ID" \
      --member="serviceAccount:$(sa_email "$role")" \
      --role=roles/secretmanager.secretAccessor
  done
done

echo
echo "# Next (owner, interactive, values never echoed): for each secret above"
echo "#   gcloud secrets versions add <secret> --project=$PROJECT_ID --data-file=-"
