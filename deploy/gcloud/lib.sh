# shellcheck shell=bash
# Shared helpers for deploy/gcloud/NN-*.sh. Sourced, never executed.
# Plan and naming: docs/engineering/deploy/DEPLOY-PLAN.md.
#
# Every script takes PROJECT_ID, REGION and ENV (staging|prod) from the environment.
# DRY_RUN=1 (default): print every gcloud command, run nothing (not even describe).
# DRY_RUN=0: run describe-before-create checks and the commands.
# Secret VALUES never pass through these scripts; the owner adds them with
#   gcloud secrets versions add NAME --data-file=-
# Compatible with macOS /bin/bash 3.2 (no associative arrays, no ${var,,}).

set -euo pipefail

DRY_RUN="${DRY_RUN:-1}"
PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-}"
ENV="${ENV:-}"

die() { echo "error: $*" >&2; exit 1; }

require_base_params() {
  [ -n "$PROJECT_ID" ] || die "PROJECT_ID is required (e.g. imageopz-prod-4821)"
  [ -n "$REGION" ] || die "REGION is required (e.g. asia-northeast1)"
  case "$DRY_RUN" in 0|1) ;; *) die "DRY_RUN must be 0 or 1" ;; esac
}

require_env_params() {
  require_base_params
  case "$ENV" in staging|prod) ;; *) die "ENV must be staging or prod" ;; esac
}

require_var() {
  # require_var NAME [hint]
  local name="$1"
  [ -n "${!name:-}" ] || die "$name is required${2:+ ($2)}"
}

# Print a command in a copy-pasteable form; run it only when DRY_RUN=0.
run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [ "$DRY_RUN" = "0" ]; then
    "$@"
  fi
}

# exists CMD...: true when a describe/get command succeeds. In dry-run nothing is executed and the resource is
# reported as absent, so the plan shows every create command (all creates are guarded by exists at run time).
exists() {
  if [ "$DRY_RUN" != "0" ]; then
    printf '# dry-run: would check'
    printf ' %q' "$@"
    printf '\n'
    return 1
  fi
  "$@" >/dev/null 2>&1
}

banner() {
  echo
  echo "### $*  (project=$PROJECT_ID region=$REGION env=${ENV:-n/a} dry_run=$DRY_RUN)"
}

# --- Naming (single project, environment-suffixed resources; DEPLOY-PLAN.md "Resource inventory") ---------------
AR_REPO="imageopz"                       # shared by both environments: the same digest is promoted staging -> prod
API_IMAGE_NAME="api"                     # Python image: api, worker and the three jobs
PAYMENTS_IMAGE_NAME="payments-ingress"   # Go image

svc_api()      { echo "imageopz-api-${ENV}"; }
svc_worker()   { echo "imageopz-worker-${ENV}"; }
svc_payments() { echo "imageopz-payments-${ENV}"; }

job_migrate() { echo "imageopz-migrate-${ENV}"; }
job_sweep()   { echo "imageopz-sweep-uploads-${ENV}"; }
job_cleanup() { echo "imageopz-cleanup-expired-${ENV}"; }

queue_jobs()     { echo "imageopz-jobs-${ENV}"; }
queue_payments() { echo "imageopz-payments-${ENV}"; }

sched_sweep()   { echo "imageopz-sweep-uploads-${ENV}"; }
sched_cleanup() { echo "imageopz-cleanup-expired-${ENV}"; }

# Service account ids (6-30 chars). One identity per service/job; see DEPLOY-PLAN.md "IAM matrix".
sa_id() { echo "iz-$1-${ENV}"; }   # sa_id api|worker|payments|tasks-invoker|migrate|sweep|cleanup|scheduler
sa_email() { echo "$(sa_id "$1")@${PROJECT_ID}.iam.gserviceaccount.com"; }

# Secret Manager secret ids per environment; DEPLOY-PLAN.md "Secrets".
secret_name() { echo "imageopz-$1-${ENV}"; }   # secret_name database-url|...

queue_path() { echo "projects/${PROJECT_ID}/locations/${REGION}/queues/$1"; }

# Deterministic Cloud Run URL (https://SERVICE-PROJECT_NUMBER.REGION.run.app); DEPLOY-PLAN.md cites the doc.
# Needed before the first deploy because the worker URL is also the OIDC audience.
project_number() {
  if [ -n "${PROJECT_NUMBER:-}" ]; then
    echo "$PROJECT_NUMBER"
  elif [ "$DRY_RUN" != "0" ]; then
    echo "PROJECT_NUMBER"
  else
    gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'
  fi
}
run_url() { echo "https://$1-$(project_number).${REGION}.run.app"; }

# --- Sizing per environment (DEPLOY-PLAN.md "Resource inventory" and "DB pool sizing (G1)") -----------------------
# Jobs queue dispatches stay one below the worker's max instances, so a payments delivery always finds a free
# worker instance (worker concurrency is 1).
if [ "$ENV" = "prod" ]; then
  API_MAX=3; WORKER_MAX=3; PAYMENTS_MAX=2
else
  API_MAX=2; WORKER_MAX=2; PAYMENTS_MAX=1
fi
JOBS_QUEUE_CONCURRENCY=$((WORKER_MAX - 1))
# shellcheck disable=SC2034  # read by the sourcing scripts
PAYMENTS_QUEUE_CONCURRENCY=1
