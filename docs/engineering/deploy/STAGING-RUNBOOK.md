# STAGING-RUNBOOK - first bring-up of ImageOpz staging on Cloud Run (owner-executed)

Generated in T-008c0 from main@fbe161a: `deploy/gcloud/*`, `.github/workflows/deploy.yml`, `image.yml`, `Dockerfile`,
[DEPLOY-PLAN.md](DEPLOY-PLAN.md), imageopz-ui `docs/engineering/deploy/UI-ENVIRONMENTS.md`. Phases 3-4 rewritten in
T-008c1 (bootstrap fixes G1-G3). Nothing in this file has been run against GCP; every `DRY_RUN=1` command was run
locally (transcript excerpts in tasks/T-008c0-staging-runbook.md and tasks/T-008c1-bootstrap.md).

How to use it
- Run the phases in order. Each phase ends with **STOP**: paste the listed output to the reviewer before continuing.
- Every script runs first with `DRY_RUN=1` (prints commands, runs nothing), then with `DRY_RUN=0`. Read the dry-run
  output before the real run: it is exactly what will execute.
- Run the scripts from the repository root as `./deploy/gcloud/NN-*.sh` (committed executable since T-008c1). If one
  fails with "permission denied" (a checkout that lost the mode, e.g. `core.fileMode=false`), run the same command with
  `bash` in front: `DRY_RUN=0 bash deploy/gcloud/NN-*.sh`.
- Run them only from a `main` checkout you have inspected (`git log -1` = the merged SHA you reviewed).
- The bootstrap order (gap G1, fixed in T-008c1): CI identity for the registry only (3.4), repository variables (3.5),
  a `build_only` workflow run pushes the first images (4.1), the owner deploys the job and services by digest
  (4.3-4.4), then the full CI identity (4.5) and the migration (4.6). The first full CI rollout follows `65` (5.1b).
- Secret values never appear on a command line, in an env file, in a paste to the reviewer, or in shell history.

Fixed values

| Name | Value |
|---|---|
| Project | `imageopz-prod-4821` (one project, `-staging` / `-prod` suffixed resources) |
| Region | `asia-northeast1` |
| Staging UI origin | `https://staging.imageopz.aindrajaya.my.id` (Q3) |
| Services | `imageopz-api-staging`, `imageopz-worker-staging`, `imageopz-payments-staging` |
| Job | `imageopz-migrate-staging` |
| Queues | `imageopz-jobs-staging`, `imageopz-payments-staging` |
| Scheduler jobs | `imageopz-sweep-uploads-staging`, `imageopz-cleanup-expired-staging` |
| Service accounts | `iz-{api,worker,payments,tasks-invoker,migrate,scheduler}-staging`, `iz-deployer-staging` |
| URLs | `https://<service>-<PROJECT_NUMBER>.asia-northeast1.run.app` (deterministic; the worker URL is the OIDC audience) |

---

## Phase 0 - Preflight (read-only)

### 0.1 gcloud configuration
```sh
gcloud --version                      # a recent SDK; if a flag below is rejected, `gcloud components update`
gcloud auth list                      # the owner account is ACTIVE
gcloud config configurations create imageopz   # optional: a dedicated configuration (skip if it exists)
gcloud config set account <owner-account>
gcloud config set project imageopz-prod-4821
gcloud config set run/region asia-northeast1
gcloud config list
gcloud projects describe imageopz-prod-4821 --format='value(projectNumber,lifecycleState)'
gcloud projects get-ancestors imageopz-prod-4821
gcloud billing projects describe imageopz-prod-4821 --format='value(billingEnabled,billingAccountName)'
```
Expected: `config list` shows the owner account, project `imageopz-prod-4821`, `run/region = asia-northeast1`;
`projects describe` prints `<PROJECT_NUMBER>  ACTIVE`; `get-ancestors` prints only the project (no `organization`
row, which answers Q10: no org policy can block `allUsers`; if an organization row appears, stop and report it);
billing prints `True  billingAccounts/XXXXXX-XXXXXX-XXXXXX`.

### 0.2 The env file the scripts read
The scripts read only environment variables. Keep one non-secret file per environment outside the repo:
```sh
cp deploy/gcloud/env.example ~/imageopz-staging.env
chmod 600 ~/imageopz-staging.env
```
Fill in (all values are public or non-secret; never put a password, token, key or signing secret here):

| Variable | Value / where it comes from | Needed by phase |
|---|---|---|
| `PROJECT_ID`, `REGION`, `ENV` | keep `imageopz-prod-4821`, `asia-northeast1`, `staging` | all |
| `DRY_RUN` | keep `1`; override per command with `DRY_RUN=0 ./deploy/gcloud/NN-*.sh` | all |
| `PROJECT_NUMBER` | from 0.1 (`projects describe`); makes dry runs print real URLs | all |
| `UI_ORIGINS` | keep `https://staging.imageopz.aindrajaya.my.id`; add `,http://localhost:3000` only if you want local UI against staging | 4 |
| `R2_ACCOUNT_ID` | Cloudflare dashboard > R2 > Account ID | 4 |
| `R2_BUCKET_NAME`, `R2_UPLOADS_BUCKET` | keep `imageopz-staging`, `imageopz-uploads-staging` | 4 |
| `R2_CUSTOM_DOMAIN` | **pending decision D1** (section "Decisions"); public base URL of the staging outputs bucket, no trailing slash | 4 |
| `SUPABASE_URL` | staging Supabase project > Project Settings > API > Project URL (`https://<staging-ref>.supabase.co`) | 4 |
| `SUPABASE_ANON_KEY` | staging project anon / publishable key (public by design). Never the service-role key. | 4 |
| `LEMONSQUEEZY_STORE_ID`, `LEMONSQUEEZY_VARIANT_ID` | LemonSqueezy in **test mode**: store id and the Pro variant id | 4 |
| `LEMONSQUEEZY_REDIRECT_URL` | where the checkout returns, on the staging UI origin | 4 |
| `API_IMAGE`, `PAYMENTS_IMAGE` | keep empty; set to the digests in 4.2, empty again in 4.7 (empty = `60` keeps the running images) | 4 |
| `TRUSTED_PROXY_HOPS` | keep `1`; Phase 6 decides | 4, 6 |
| `GITHUB_REPOSITORY_ID`, `GITHUB_OWNER_ID` | `gh api repos/aindrajaya/imageopz-server --jq '.id, .owner.id'` (3.3) | 3 |
| `NOTIFICATION_CHANNEL` | Phase 5 (email channel id) | 5 |
| `BILLING_ACCOUNT` | the `XXXXXX-XXXXXX-XXXXXX` part from 0.1 | 1 (budget) |
| `BUDGET_AMOUNT` | keep `10USD` | 1 |
| `EXECUTE_MIGRATE` | keep `0`; Phase 4.6 passes `1` on the command line | 4 |
| `PAUSE`, `SWEEP_SCHEDULE`, `CLEANUP_SCHEDULE`, `CLEANUP_DRY_RUN`, `DEPLOY_WORKFLOW` | keep the defaults | - |

Load it in every new shell before running a script:
```sh
cd <path>/imageopz-server && git switch main && git pull --ff-only && git log -1 --oneline
set -a; . ~/imageopz-staging.env; set +a
echo "$PROJECT_ID $REGION $ENV dry_run=$DRY_RUN pn=$PROJECT_NUMBER"
```

### 0.3 Budget check
Billing > Budgets & alerts in the console: note whether a budget for `imageopz-prod-4821` exists. If none exists, it is
created in Phase 1.4 (before anything billable). Billing > Reports should show ~0 for the project today.

### 0.4 Owner prerequisites outside GCP (check, do not create anything new in this phase)
- Cloudflare R2: buckets `imageopz-staging` and `imageopz-uploads-staging` exist; uploads bucket CORS and the
  `uploads/` 1-day lifecycle rule are set (README "Owner steps outside gcloud").
- Supabase: the staging project exists (Q7), region ap-northeast-1, not paused.
- LemonSqueezy: test mode available on the store.

**STOP 0** - paste: `gcloud config list`, the `projects describe`, `get-ancestors` and `billing projects describe`
lines, the `echo` line of 0.2, and whether a budget exists. Do not paste the env file.

---

## Phase 1 - Foundations

### 1.1 `00-enable-apis.sh` (project-wide)
```sh
./deploy/gcloud/00-enable-apis.sh                 # DRY_RUN=1: prints one `gcloud services enable` with 12 APIs
DRY_RUN=0 ./deploy/gcloud/00-enable-apis.sh
```
Verify:
```sh
gcloud services list --enabled --project="$PROJECT_ID" --format='value(config.name)' \
  | grep -E '^(run|artifactregistry|cloudtasks|cloudscheduler|secretmanager|iam|iamcredentials|sts|cloudresourcemanager|logging|monitoring|billingbudgets)\.googleapis\.com$' | sort
```
Expected: exactly those 12 names.

### 1.2 `10-artifact-registry.sh` (project-wide)
```sh
./deploy/gcloud/10-artifact-registry.sh           # create repo `imageopz` + cleanup policy with --dry-run
DRY_RUN=0 ./deploy/gcloud/10-artifact-registry.sh
```
Verify:
```sh
gcloud artifacts repositories describe imageopz --project="$PROJECT_ID" --location="$REGION" \
  --format='yaml(format,cleanupPolicyDryRun,cleanupPolicies)'
```
Expected: `format: DOCKER`, `cleanupPolicyDryRun: true`, the policies from `deploy/gcloud/ar-cleanup-policy.json`.
Keep `CLEANUP_DRY_RUN=1` until the policy's dry-run logs have been reviewed (not part of staging bring-up).

### 1.3 `20-service-accounts.sh` (staging)
```sh
./deploy/gcloud/20-service-accounts.sh            # 6 x describe-check + create
DRY_RUN=0 ./deploy/gcloud/20-service-accounts.sh
```
Verify:
```sh
gcloud iam service-accounts list --project="$PROJECT_ID" --filter='email~-staging@' --format='value(email)' | sort
for sa in api worker payments tasks-invoker migrate scheduler; do
  gcloud iam service-accounts keys list --iam-account="iz-$sa-staging@$PROJECT_ID.iam.gserviceaccount.com" \
    --managed-by=user --format='value(name)'; done
```
Expected: six emails `iz-{api,migrate,payments,scheduler,tasks-invoker,worker}-staging@...`; the key loop prints
nothing (no user-managed keys, ever).

### 1.4 Budget before spend (`90-observability.sh`, budget part only)
`90` creates the budget when `BILLING_ACCOUNT` is set and skips alert policies while `NOTIFICATION_CHANNEL` is empty.
Running it now also creates the log-based metric `imageopz-worker-5xx-staging` (free, harmless before the worker
exists). Phase 5 runs it again for the alert policies (the budget and metric are then reported as existing / updated).
```sh
NOTIFICATION_CHANNEL= ./deploy/gcloud/90-observability.sh
NOTIFICATION_CHANNEL= DRY_RUN=0 ./deploy/gcloud/90-observability.sh
```
Verify: `gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" --format='value(displayName,amount.specifiedAmount)'`
shows `imageopz imageopz-prod-4821` with 10 USD; console Budgets shows thresholds 50/90/100 %. If a budget already
existed (0.3), the script prints `# exists:` and changes nothing.

**STOP 1** - paste: the verification output of 1.1-1.4 and the last 5 lines of each `DRY_RUN=0` run.

---

## Phase 2 - Secrets

### 2.1 `30-secrets.sh` (containers + per-secret accessor bindings, no values)
```sh
./deploy/gcloud/30-secrets.sh                     # 9 secrets, 16 accessor bindings
DRY_RUN=0 ./deploy/gcloud/30-secrets.sh
```

### 2.2 Add each value (never echoed, never in history)
Define this helper once in the shell (zsh or bash). It reads the value with echo off, sends it on stdin, and forgets it:
```sh
add_secret() {  # add_secret <secret-id>
  local v
  printf 'value for %s (hidden, Enter to submit): ' "$1"; read -rs v; echo
  [ -n "$v" ] || { echo "empty, nothing added"; return 1; }
  printf '%s' "$v" | gcloud secrets versions add "$1" --project="$PROJECT_ID" --data-file=-
  unset v
}
```
Alternative for a value already in the clipboard: `pbpaste | tr -d '\n' | gcloud secrets versions add <id>
--project="$PROJECT_ID" --data-file=-`, then clear the clipboard with `pbcopy < /dev/null`.
`printf '%s'` and `tr -d '\n'` matter: a trailing newline inside a URL or key breaks it.

| Secret id | Value source | How |
|---|---|---|
| `imageopz-database-url-staging` | Staging Supabase > Connect > **Transaction pooler** (port 6543). Form: `postgresql://postgres.<staging-ref>:<db-password>@<pooler-host>:6543/postgres?sslmode=require`. Bare `postgresql://` only (never `postgresql+psycopg://`: the Go ingress shares it and pgx rejects that scheme). User = `postgres.<ref>` (table owner). Unknown password: Project Settings > Database > reset. | `add_secret` |
| `imageopz-database-url-session-staging` | Same project, **Session pooler** (port 5432), same user and password, same form ending `:5432/postgres?sslmode=require`. Used only by the migrate job. | `add_secret` |
| `imageopz-secret-key-staging` | Generated, never seen: `openssl rand -hex 32 \| tr -d '\n' \| gcloud secrets versions add imageopz-secret-key-staging --project="$PROJECT_ID" --data-file=-` | generated |
| `imageopz-r2-outputs-access-key-id-staging` / `...-secret-access-key-staging` | Cloudflare R2 > Manage API tokens > Create: Object Read & Write, **only** bucket `imageopz-staging`. Access Key ID and Secret Access Key are shown once. | `add_secret` x2 |
| `imageopz-r2-uploads-access-key-id-staging` / `...-secret-access-key-staging` | A second token, Object Read & Write, **only** bucket `imageopz-uploads-staging`. | `add_secret` x2 |
| `imageopz-lemonsqueezy-api-key-staging` | LemonSqueezy, **test mode on**: Settings > API > new key (a test-mode key). | `add_secret` |
| `imageopz-lemonsqueezy-signing-secret-staging` | Generated here, entered in the LemonSqueezy webhook form in Phase 7: `openssl rand -hex 20 \| tr -d '\n' \| gcloud secrets versions add imageopz-lemonsqueezy-signing-secret-staging --project="$PROJECT_ID" --data-file=-` (40 hex chars; LemonSqueezy limits the signing-secret length, believed 40, check the form). | generated |

Not provisioned (by design, DEPLOY-PLAN section 5): `INTERNAL_AUTH_SHARED_SECRET` / `INTERNAL_SHARED_SECRET` (the
worker uses OIDC, not a shared secret), `RESEND_API_KEY`, `SUPABASE_ANON_KEY` (public, plain config). The only
`openssl rand` values are the SECRET_KEY and the LemonSqueezy signing secret.

### 2.3 Verify without printing values
```sh
for s in database-url database-url-session secret-key r2-outputs-access-key-id r2-outputs-secret-access-key \
         r2-uploads-access-key-id r2-uploads-secret-access-key lemonsqueezy-signing-secret lemonsqueezy-api-key; do
  printf '%-38s ' "$s"
  gcloud secrets versions list "imageopz-$s-staging" --project="$PROJECT_ID" --filter='state=ENABLED' \
    --format='value(name)' | wc -l | tr -d ' '
done
# Shape checks that print only a match count or the matched port, never the value (checklist item 24):
for s in database-url database-url-session; do
  printf '%-22s scheme_ok=%s port=%s\n' "$s" \
    "$(gcloud secrets versions access latest --secret="imageopz-$s-staging" --project="$PROJECT_ID" | grep -c '^postgresql://')" \
    "$(gcloud secrets versions access latest --secret="imageopz-$s-staging" --project="$PROJECT_ID" | grep -oE ':(5432|6543)/' | tr -d ':/')"
done
gcloud secrets get-iam-policy imageopz-database-url-staging --project="$PROJECT_ID" --format='value(bindings.members)'
```
Expected: every secret shows `1`; `database-url scheme_ok=1 port=6543`, `database-url-session scheme_ok=1 port=5432`;
the database-url policy lists `iz-api-staging`, `iz-worker-staging`, `iz-payments-staging`.

**STOP 2** - paste: the 2.3 output only (counts, ports, members).

---

## Phase 3 - Queues, GitHub Environment, CI identity (registry stage), repository variables

### 3.1 `40-queues.sh`
```sh
./deploy/gcloud/40-queues.sh
DRY_RUN=0 ./deploy/gcloud/40-queues.sh
```
The last step grants Service Account User on `iz-tasks-invoker-staging` to the Cloud Tasks service agent
`service-<PROJECT_NUMBER>@gcp-sa-cloudtasks.iam.gserviceaccount.com`. Google creates that agent when the API is enabled,
usually but not always immediately; if this step fails with "does not exist", stop and report (gap G4) instead of
creating the agent by hand.
Verify:
```sh
for q in imageopz-jobs-staging imageopz-payments-staging; do
  gcloud tasks queues describe "$q" --project="$PROJECT_ID" --location="$REGION" \
    --format='yaml(name.basename(),state,rateLimits,retryConfig)'; done
gcloud tasks queues get-iam-policy imageopz-jobs-staging --project="$PROJECT_ID" --location="$REGION" --format='value(bindings.members)'
gcloud iam service-accounts get-iam-policy "iz-tasks-invoker-staging@$PROJECT_ID.iam.gserviceaccount.com" --format='value(bindings.members)'
```
Expected: both `RUNNING`; jobs: `maxConcurrentDispatches: 1`, `maxDispatchesPerSecond: 2.0`, `maxAttempts: 15`,
`maxRetryDuration: 7200s`, `minBackoff: 30s`, `maxBackoff: 600s`, `maxDoublings: 5`; payments: `maxConcurrentDispatches: 1`,
`maxAttempts: 50`, `maxRetryDuration: 604800s`, `minBackoff: 10s`, `maxBackoff: 600s`. Jobs queue enqueuers: api and
worker. Invoker SA users: api, worker, payments and the Cloud Tasks service agent.

### 3.2 GitHub Environment `production` (must exist before any repository variable)
The deploy workflow's `production` job names `environment: production`. If the Environment does not exist when the
variables are set, GitHub creates it on the fly **without** a required reviewer, and the prod job would run unapproved.
So create it first: GitHub > imageopz-server > Settings > Environments > New environment `production` > Required
reviewers: the owner; Deployment branches: `main` only. Verify:
```sh
gh api repos/aindrajaya/imageopz-server/environments/production --jq '.protection_rules[] | .type'
```
Expected: `required_reviewers` (and `branch_policy` if branches were restricted). Do not continue to 3.5 without it.

### 3.3 Read-only inputs
```sh
gh api repos/aindrajaya/imageopz-server --jq '.id, .owner.id'     # -> GITHUB_REPOSITORY_ID, GITHUB_OWNER_ID in the env file
gh variable list --repo aindrajaya/imageopz-server                  # expected: empty (the deploy workflow stays dormant)
```
Reload the env file after editing it.

### 3.4 `80-wif.sh`, registry stage
`WIF_STAGE=registry` creates the pool, the GitHub provider (condition: this repository by numeric id, `refs/heads/main`,
and `.github/workflows/deploy.yml@refs/heads/main`), `iz-deployer-staging`, its `workloadIdentityUser` binding and
`artifactregistry.writer` on the `imageopz` repository. Nothing Cloud Run is touched (no service exists yet). The full
stage runs in Phase 4.5.
```sh
WIF_STAGE=registry ./deploy/gcloud/80-wif.sh
WIF_STAGE=registry DRY_RUN=0 ./deploy/gcloud/80-wif.sh
```
Keep the two `# GitHub Actions ... inputs` lines it prints (provider path and deployer email). Verify:
```sh
gcloud iam workload-identity-pools providers describe imageopz-server --project="$PROJECT_ID" --location=global \
  --workload-identity-pool=github --format='value(state,attributeCondition)'
gcloud artifacts repositories get-iam-policy imageopz --project="$PROJECT_ID" --location="$REGION" --format='value(bindings)'
```
Expected: `ACTIVE` and the condition with your two numeric ids, `refs/heads/main` and the deploy workflow path;
`roles/artifactregistry.writer` for `serviceAccount:iz-deployer-staging@...`.

### 3.5 Repository variables (only after 3.2 shows `required_reviewers`)
Names and values are not secret; the provider path and deployer email come from 3.4.
```sh
gh api repos/aindrajaya/imageopz-server/environments/production --jq '.protection_rules[] | .type'   # again: required_reviewers
gh variable set GCP_WIF_PROVIDER     --repo aindrajaya/imageopz-server --body "projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/imageopz-server"
gh variable set GCP_PROJECT_ID       --repo aindrajaya/imageopz-server --body "imageopz-prod-4821"
gh variable set GCP_REGION           --repo aindrajaya/imageopz-server --body "asia-northeast1"
gh variable set GCP_DEPLOYER_STAGING --repo aindrajaya/imageopz-server --body "iz-deployer-staging@imageopz-prod-4821.iam.gserviceaccount.com"
gh variable set GCP_DEPLOYER_PROD    --repo aindrajaya/imageopz-server --body "iz-deployer-prod@imageopz-prod-4821.iam.gserviceaccount.com"
gh variable list --repo aindrajaya/imageopz-server
```
From this moment every push to `main` runs the full deploy workflow. **Do not merge anything to `main` until Phase 5.1
is done**: the run's `staging` job would fail (no services or run.developer before 4.5; the smoke test needs the public
api from 5.1). After that, every push deploys
staging and then **waits for prod approval**: reject every `production` approval until the prod bring-up (prod
resources do not exist).

**STOP 3** - paste: the 3.1 verification, the 3.2 `gh api` output, the 3.4 verification, `gh variable list`.

---

## Phase 4 - First image, migrate job, services, CI identity (full stage), migration

### 4.1 First build only (`workflow_dispatch`, `build_only=true`)
Runs `tests` and `build` (both images pushed once, tagged with the main commit SHA), prints the digests, deploys nothing.
```sh
gh workflow run deploy.yml --repo aindrajaya/imageopz-server --ref main -f build_only=true
gh run list --repo aindrajaya/imageopz-server --workflow=deploy.yml --limit 1     # note the run id
gh run watch <run-id> --repo aindrajaya/imageopz-server --exit-status
```
Expected: `tests` and `build` green; `staging` and `production` skipped. The run summary prints two lines ready for the
env file:
```
API_IMAGE='asia-northeast1-docker.pkg.dev/imageopz-prod-4821/imageopz/api@sha256:<d1>'
PAYMENTS_IMAGE='asia-northeast1-docker.pkg.dev/imageopz-prod-4821/imageopz/payments-ingress@sha256:<d2>'
```
Cross-check from Artifact Registry (the SHA is the run's commit):
```sh
SHA=$(git rev-parse origin/main)
for img in api payments-ingress; do
  gcloud artifacts docker images describe "$REGION-docker.pkg.dev/$PROJECT_ID/imageopz/$img:$SHA" --format='value(image_summary.digest)'
done
```
A `build` failure at the auth step ("unauthorized" / "permission denied" from STS) means the provider condition did not
match: stop and paste the error (not the token).

### 4.2 Digests into the env file
Put the two lines from 4.1 into `~/imageopz-staging.env` (replacing the empty `API_IMAGE=''` / `PAYMENTS_IMAGE=''`),
then reload it: `set -a; . ~/imageopz-staging.env; set +a; echo "$API_IMAGE"`.

### 4.3 `50-jobs.sh`: deploy the migrate job (no execution yet)
```sh
./deploy/gcloud/50-jobs.sh
DRY_RUN=0 ./deploy/gcloud/50-jobs.sh
gcloud run jobs describe imageopz-migrate-staging --project="$PROJECT_ID" --region="$REGION" \
  --format='value(spec.template.spec.template.spec.containers[0].image)'
```
Expected: the image is `API_IMAGE` from 4.2. `EXECUTE_MIGRATE` stays `0` here; the migration runs in 4.6.

### 4.4 `60-deploy-services.sh`: the three services
The services may start before the migration: `/ready` checks only the database connection (`SELECT 1` / ping), and
api and payments take no public traffic before `65` (Phase 5.1); the worker has no invoker yet either.
```sh
./deploy/gcloud/60-deploy-services.sh          # read the three env-file blocks and the "# <service>: image from ..." lines
DRY_RUN=0 ./deploy/gcloud/60-deploy-services.sh
```
Expected in the output: `# imageopz-<s>-staging: image from API_IMAGE` (worker, api) and `... from PAYMENTS_IMAGE`
(payments); `TRUSTED_PROXY_HOPS: '1'` in the api block. Verify:
```sh
gcloud run services list --project="$PROJECT_ID" --region="$REGION" --filter='metadata.name~-staging$' \
  --format='table(metadata.name,status.conditions[0].status,metadata.annotations."run.googleapis.com/ingress")'
for s in api worker payments; do
  gcloud run services describe "imageopz-$s-staging" --project="$PROJECT_ID" --region="$REGION" \
    --format='value(metadata.annotations."run.googleapis.com/urls")'; done
```
Expected: three services `True` (the startup probe on `/ready` passed, so each reached the database); ingress
`all` / `internal` / `all`; each URL list contains `https://imageopz-<s>-staging-<PROJECT_NUMBER>.asia-northeast1.run.app`.
The worker's deterministic URL must be present: it is the OIDC audience in the worker, api and payments config.

### 4.5 `80-wif.sh`, full stage
`WIF_STAGE=all` (the default) first checks that the three services and the migrate job exist and stops with a clear
message before changing anything if one is missing; then it re-applies the registry part and adds `run.developer` on
the services and the job, and actAs on the four runtime identities.
```sh
WIF_STAGE=all ./deploy/gcloud/80-wif.sh
WIF_STAGE=all DRY_RUN=0 ./deploy/gcloud/80-wif.sh
gcloud run services get-iam-policy imageopz-api-staging --project="$PROJECT_ID" --region="$REGION" --format='value(bindings)'
gcloud run jobs get-iam-policy imageopz-migrate-staging --project="$PROJECT_ID" --region="$REGION" --format='value(bindings)'
```
Expected: `roles/run.developer` for `iz-deployer-staging` on each service and on the job.

### 4.6 Migrate execution against the staging Supabase (session pooler, table owner)
```sh
EXECUTE_MIGRATE=1 ./deploy/gcloud/50-jobs.sh
EXECUTE_MIGRATE=1 DRY_RUN=0 ./deploy/gcloud/50-jobs.sh     # re-applies the job (same image), then executes and waits
```
Verify alembic is at head:
```sh
gcloud run jobs executions list --job=imageopz-migrate-staging --project="$PROJECT_ID" --region="$REGION" --limit=1 \
  --format='table(name.basename(),status.succeededCount,status.failedCount,status.completionTime)'
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="imageopz-migrate-staging"' \
  --project="$PROJECT_ID" --freshness=1h --limit=50 --format='value(textPayload)' | grep -i 'running upgrade'
grep -h "^revision" alembic/versions/0*.py | sort | tail -1     # repo head, no code executed
```
Then in the staging Supabase SQL editor (read-only queries):
```sql
select version_num from alembic_version;
select relname, relrowsecurity from pg_class
 where relnamespace = 'public'::regnamespace and relkind = 'r' order by relname;
```
Expected: execution succeeded 1 / failed 0; logs show `Running upgrade  -> 0001` through `0005 -> 0006`;
`version_num = 0006` = repo head `'0006'`; every table (including `alembic_version`) has `relrowsecurity = t`.
Also Supabase > Settings > API > Exposed schemas: `public` only, not `payments` (checklist item 5).

### 4.7 Empty the digests in the env file again
From now on CI moves the images. Set `API_IMAGE=''` and `PAYMENTS_IMAGE=''` in `~/imageopz-staging.env` and reload it.
A later `60` run then keeps the image each service runs now (it reads it with `describe`) instead of rolling back to
the bootstrap digests. `50` needs `API_IMAGE` explicitly; set it only for that run, to the image the services run
(Phase 7.2 shows how to read it).

**STOP 4** - paste after 4.1, 4.4, 4.5 and 4.6: the verification output (digests are fine to paste).

---

## Phase 5 - IAM, Scheduler, Observability

### 5.1 `65-service-iam.sh`
```sh
./deploy/gcloud/65-service-iam.sh
DRY_RUN=0 ./deploy/gcloud/65-service-iam.sh
for s in worker api payments; do echo "== $s"
  gcloud run services get-iam-policy "imageopz-$s-staging" --project="$PROJECT_ID" --region="$REGION" \
    --flatten='bindings[].members' --filter='bindings.role=roles/run.invoker' --format='value(bindings.members)'; done
```
Expected: worker `serviceAccount:iz-tasks-invoker-staging@...` and `serviceAccount:iz-scheduler-staging@...` only;
api and payments `allUsers`. An error mentioning "permitted customer" or domain restricted sharing = an org policy
(Q10): stop.

### 5.1b First CI rollout (validates the CI path, the S7 IAM edge for `jobs execute --wait`, and the smoke test)
Only now: the smoke test in `ci-rollout.sh` needs the public api and payments from 5.1 (before it, every path is 403).
```sh
gh workflow run deploy.yml --repo aindrajaya/imageopz-server --ref main
gh run list --repo aindrajaya/imageopz-server --workflow=deploy.yml --limit 1
gh run watch <run-id> --repo aindrajaya/imageopz-server
```
Expected: `tests`, `build`, `staging` green (migrate is a no-op at head; smoke test passes); `production` waits for
approval: **reject it**. If `staging` fails on `jobs execute --wait` or operation polling with a permission error,
that is S7: stop and report the exact error (the plan's fallback, project-level run.developer for the deployers,
is a recorded deviation the reviewer decides on). From here on merges to `main` are fine again (Phase 3.5).

### 5.2 `70-scheduler.sh` (staging is created paused)
```sh
./deploy/gcloud/70-scheduler.sh
DRY_RUN=0 ./deploy/gcloud/70-scheduler.sh
gcloud scheduler jobs list --project="$PROJECT_ID" --location="$REGION" \
  --format='table(name.basename(),state,schedule,httpTarget.uri)'
```
Expected: both jobs `PAUSED`, `*/5 * * * *` and `17 * * * *`, URIs on the worker's deterministic URL.

Run one sweep and one cleanup by hand (checklist item 4, U2). `jobs run` on a paused job is not documented here, so
resume, run, pause:
```sh
for j in imageopz-sweep-uploads-staging imageopz-cleanup-expired-staging; do
  gcloud scheduler jobs resume "$j" --project="$PROJECT_ID" --location="$REGION"
  gcloud scheduler jobs run "$j" --project="$PROJECT_ID" --location="$REGION"
done
# wait ~1 min, then:
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="imageopz-worker-staging" AND httpRequest.requestUrl:"/internal/maintenance/"' \
  --project="$PROJECT_ID" --freshness=15m --limit=5 --format='table(timestamp,httpRequest.requestMethod,httpRequest.requestUrl,httpRequest.status)'
for j in imageopz-sweep-uploads-staging imageopz-cleanup-expired-staging; do
  gcloud scheduler jobs pause "$j" --project="$PROJECT_ID" --location="$REGION"; done
```
Expected: one `POST .../internal/maintenance/sweep 200` and one `.../cleanup 200`. 401 = the worker's OIDC check
(audience or allowed emails) rejected the scheduler token; 403 from the front end = run.invoker missing; 404 = the
call did not count as internal ingress. Each is a stop. Re-running `70` later converges to paused again.

### 5.3 `90-observability.sh` (alert policies)
Create an email notification channel first: Monitoring > Alerting > Edit notification channels > Email. Get its id:
`gcloud beta monitoring channels list --project="$PROJECT_ID" --format='value(name,displayName)'` and set
`NOTIFICATION_CHANNEL` in the env file. Then:
```sh
./deploy/gcloud/90-observability.sh
DRY_RUN=0 ./deploy/gcloud/90-observability.sh
gcloud monitoring policies list --project="$PROJECT_ID" --filter='displayName:"imageopz staging"' --format='value(displayName,enabled)'
```
Expected: 7 policies (payment event failed at the API, payments ingress 5xx, application ERROR logs, migrate job error,
worker 5xx burst, jobs queue backlog, payments queue not draining), all `True`. If gcloud answers "Invalid choice:
'policies'" (the command group may be beta-only in your SDK), stop: that is a script gap to fix, not to work around.

**STOP 5** - paste: the three verification outputs, the 5.1b run result, and the maintenance log table.

---

## Phase 6 - Verification

```sh
API="https://imageopz-api-staging-$PROJECT_NUMBER.$REGION.run.app"
WORKER="https://imageopz-worker-staging-$PROJECT_NUMBER.$REGION.run.app"
PAY="https://imageopz-payments-staging-$PROJECT_NUMBER.$REGION.run.app"
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
curl -sS "$API/health"; echo; curl -sS "$API/ready"; echo; curl -sS "$API/version"; echo
echo "api docs (staging on)   $(code "$API/docs")"
for p in /internal/jobs/process /internal/payments/events /internal/maintenance/sweep /internal/maintenance/cleanup; do
  echo "api POST $p -> $(code -X POST "$API$p")"; done
echo "worker GET /health -> $(code "$WORKER/health")"
echo "worker POST /internal/jobs/process -> $(code -X POST "$WORKER/internal/jobs/process")"
echo "worker with a Google identity token -> $(code -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$WORKER/internal/jobs/process")"
echo "payments /health $(code "$PAY/health")  /ready $(code "$PAY/ready")  unsigned webhook $(code -X POST -d '{}' "$PAY/webhooks/lemonsqueezy")"
```
Expected: `{"status":"healthy","version":"1.0.0"}`, `{"status":"ready"}`, `{"version":"1.0.0","name":"ImageOpz Backend"}`;
docs 200; every api `/internal/*` 404 (route not mounted, `SERVICE_ROLE=api`); worker 404 for all three calls from
the internet, including with a valid identity token (Google front end rejects non-internal traffic before IAM or the
app; a 401/403 from the worker would mean public ingress is open: stop); payments 200, 200, 401.

CORS preflight from the staging origin and from a foreign one:
```sh
for o in https://staging.imageopz.aindrajaya.my.id https://evil.example; do echo "== $o"
  curl -s -D - -o /dev/null -X OPTIONS "$API/api/v1/uploads/presign" -H "Origin: $o" \
    -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: content-type,x-session-id' \
    | grep -i '^access-control-allow-origin' || echo "(no allow-origin)"; done
```
Expected: the staging origin is echoed; the foreign origin gets none.

### X-Forwarded-For shape (X1, checklist item 15)
```sh
curl -s -o /dev/null "$API/api/v1/session/me"                                         # no client XFF
curl -s -o /dev/null -H 'X-Forwarded-For: 203.0.113.7' "$API/api/v1/session/me"     # one forged entry
sleep 20
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="imageopz-api-staging" AND textPayload:"xff_entries="' \
  --project="$PROJECT_ID" --freshness=10m --limit=4 --format='value(timestamp,textPayload)'
```
Console alternative: Logging > Logs Explorer, same query, field `textPayload`. The line logs a count, never addresses.
Read the two newest `GET /api/v1/session/me` lines: A = `xff_entries` without a client header, B = with the forged
entry (expected A + 1).

| A | Meaning | Action |
|---|---|---|
| 1 | Google's front end appends one entry (the client) | keep `TRUSTED_PROXY_HOPS=1` (current); close X1 |
| 2 | Shape is `<client>,<front-end>` | set `TRUSTED_PROXY_HOPS='2'` in the env file, run `60` (dry run first; digests stay empty, 4.7), re-run this check |
| 0 or B != A + 1 | Unexpected | stop and report both lines |

**STOP 6** - paste: the full output of this phase (no secrets in it), and the two log lines.

---

## Phase 7 - UI wiring

### 7.1 Vercel (imageopz-ui, UI-ENVIRONMENTS.md "Owner steps")
1. Branch `staging` exists on imageopz-ui; Vercel domain `staging.imageopz.aindrajaya.my.id` assigned to it; DNS record
   per UI-ENVIRONMENTS step 3.
2. Preview variables scoped to branch `staging`:

| Variable | Value |
|---|---|
| `VITE_IMAGEOPZ_API_BASE_URL` | `https://imageopz-api-staging-<PROJECT_NUMBER>.asia-northeast1.run.app` (no trailing slash) |
| `VITE_IMAGEOPZ_SEND_COOKIES` | `false` |
| `VITE_IMAGEOPZ_USE_VITE_PROXY` | unset |
| `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` | staging Supabase project URL / anon key |
| `VITE_SUPABASE_REDIRECT_URL` | `https://staging.imageopz.aindrajaya.my.id/auth/callback` |
| `VITE_PUBLIC_POSTHOG_PROJECT_TOKEN` / `_HOST` | unset |

3. Generic Preview variables for other branches as in UI-ENVIRONMENTS ("Preview"); previews will hit CORS errors by design.
4. Redeploy the `staging` branch; the page must not show "ImageOpz is not configured".

### 7.2 API CORS
`CORS_ALLOW_ORIGINS` comes from `UI_ORIGINS` in the env file and is already set by Phase 4.4. To change it, edit
`UI_ORIGINS` and re-run `60` (dry run first). Keep `API_IMAGE` / `PAYMENTS_IMAGE` empty (4.7): `60` then keeps the
image each service runs now; a digest left in the env file would roll the services back to it. To see what runs:
```sh
gcloud run services describe imageopz-api-staging --project="$PROJECT_ID" --region="$REGION" --format='value(spec.template.spec.containers[0].image)'
gcloud run services describe imageopz-payments-staging --project="$PROJECT_ID" --region="$REGION" --format='value(spec.template.spec.containers[0].image)'
```
`CORS_ALLOW_CREDENTIALS` stays `true` until T-008b3 (UI1) is live on staging.

### 7.3 Supabase staging Auth
Authentication > URL Configuration: Site URL `https://staging.imageopz.aindrajaya.my.id`; Redirect URLs
`https://staging.imageopz.aindrajaya.my.id/auth/callback` (plus `http://localhost:3000/auth/callback` only if wanted).

### 7.4 R2
- Uploads bucket `imageopz-uploads-staging`: CORS already set; confirm it lists `https://staging.imageopz.aindrajaya.my.id`
  (and `http://localhost:3000`), methods `PUT, GET`, header `content-type`, max-age 3600; lifecycle `uploads/` 1 day (U3).
- Outputs bucket `imageopz-staging`: per decision D1.

### 7.5 LemonSqueezy webhook (test mode)
Settings > Webhooks (test mode) > add: URL `https://imageopz-payments-staging-<PROJECT_NUMBER>.asia-northeast1.run.app/webhooks/lemonsqueezy`;
events `order_created`, `order_refunded`; signing secret = the value in Secret Manager, copied without display:
```sh
gcloud secrets versions access latest --secret=imageopz-lemonsqueezy-signing-secret-staging --project="$PROJECT_ID" | pbcopy
# paste into the form, save, then:
pbcopy < /dev/null
```

**STOP 7** - paste: a screenshot or description of the staging page loading (no config error), the 7.2 image lines,
and the webhook's URL and events (not the secret).

---

## Phase 8 - End to end (staging UI)

Use the Phase 6 shell (`API`, `PAY`, `code` defined) or define them again. Set a timestamp first:
`T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)`.

1. **Anonymous, small image** (< 1 MB JPEG/PNG) in a private window: presign, PUT to R2, complete, worker DONE,
   download. Credits drop from 3 to 2 (`GET /api/v1/session/me` in the UI). The download URL must open (D1).
2. **Anonymous, ~40 MB image: expected rejection.** The free tier limit is 10 MB (`free_tier_max_file_size`), so the
   UI or `/presign` must refuse it without spending a credit. A success here is a defect.
3. **LemonSqueezy test-mode purchase**: sign in with a magic link (staging Supabase), buy Pro with the test card
   (`4242 4242 4242 4242`, any future date, any CVC). Path: LemonSqueezy -> payments ingress (2xx) -> payments queue ->
   worker `/internal/payments/events` -> activation. Check:
   ```sh
   gcloud logging read 'resource.type="cloud_run_revision" AND (resource.labels.service_name="imageopz-payments-staging" OR resource.labels.service_name="imageopz-worker-staging") AND (httpRequest.requestUrl:"/webhooks/lemonsqueezy" OR httpRequest.requestUrl:"/internal/payments/events")' \
     --project="$PROJECT_ID" --freshness=30m --format='table(timestamp,resource.labels.service_name,httpRequest.status)'
   gcloud tasks list --queue=imageopz-payments-staging --project="$PROJECT_ID" --location="$REGION"   # expected: empty (drained)
   ```
   In the UI the account shows Pro. Supabase SQL: `select status, count(*) from orders group by 1;` shows one paid order.
   Then LemonSqueezy > the webhook > resend the `order_created` delivery: the ingress answers 200 and the event is a
   `duplicate` (no second activation, no new payments task).
4. **Pro, ~40 MB image** (and, for checklist item 10, if available, a ~50 MB / ~179 MP image): upload, DONE,
   download. Measure:
   ```sh
   gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"imageopz-worker-staging\" AND httpRequest.requestUrl:\"/internal/jobs/process\" AND timestamp>=\"$T0\"" \
     --project="$PROJECT_ID" --format='table(timestamp,httpRequest.status,httpRequest.latency)'
   ```
   Processing time = the 200 delivery's `latency`. Memory and CPU: Cloud Run > `imageopz-worker-staging` > Metrics >
   "Container memory utilization" and "Container CPU utilization" (max over the job's minutes; 100 % of 4 GiB = OOM risk).
   Also check for 503s (live lease, J3) and any 500 or container restart.
5. **Job states and credits**: every job ends DONE (or REJECTED for 2); no job stuck PENDING/PROCESSING
   (`gcloud tasks list --queue=imageopz-jobs-staging ...` empty). Pro credits drop by the number of images processed.
6. **Kill switch drill** (Q8):
   ```sh
   ./deploy/gcloud/99-kill-switch.sh stop
   DRY_RUN=0 ./deploy/gcloud/99-kill-switch.sh stop
   echo "api $(code "$API/health")  payments $(code "$PAY/health")"     # expected: 404 404
   for q in imageopz-jobs-staging imageopz-payments-staging; do
     gcloud tasks queues describe "$q" --project="$PROJECT_ID" --location="$REGION" --format='value(state)'; done   # PAUSED PAUSED
   DRY_RUN=0 ./deploy/gcloud/99-kill-switch.sh resume
   echo "api $(code "$API/health")  payments $(code "$PAY/health")"     # expected: 200 200 (allow ~1 min)
   ```
   Queues back to `RUNNING`; staging schedules stay `PAUSED` (resume prints "left paused"). The UI works again.

**STOP 8** - paste: results of 1-6 (states, credits, status codes), the latency table, the memory/CPU peaks, any
error lines.

---

## Decisions (owner; not decided here)

### D1 - How staging serves output images
The worker stores `r2_url = R2_CUSTOM_DOMAIN + "/optimized/<job_id>.avif"` at processing time
(`app/services/image_service.py`, returned by `/status`), so today the outputs bucket must be publicly readable at a
fixed base URL. The task premise is that DNS for `aindrajaya.my.id` is on Vercel; imageopz-ui's UI-ENVIRONMENTS.md
(step 3) says the record is added in Cloudflare DNS. Check first: `dig +short NS aindrajaya.my.id`. If the nameservers
are Cloudflare's and the zone is in the same account as R2, option C is just an R2 custom domain.

| Option | What | For | Against |
|---|---|---|---|
| A. r2.dev public URL | Enable the bucket's public development URL; `R2_CUSTOM_DOMAIN=https://pub-<id>.r2.dev` | No code, no DNS, minutes to set up; staging only | Cloudflare positions r2.dev for non-production use (rate-limited, no cache or WAF); the whole bucket is public (keys are unguessable job UUIDs); does not answer prod, so prod needs B or C anyway; URLs are stored per job, so switching later leaves old rows pointing at r2.dev (staging, acceptable) |
| B. Presigned GET | Bucket stays private; the API signs a short-lived GET when `/status` returns a DONE job | Same in staging and prod, no DNS dependency, no public bucket, revocable by expiry | Code change and tests (sign at read time instead of storing the URL; the worker would store only the key), UI must tolerate expiring links; a task before staging E2E |
| C. Cloudflare-hosted zone + R2 custom domain | `files-staging.imageopz.<domain>` as an R2 custom domain (needs the zone on Cloudflare in the R2 account) | Production-grade (cache, WAF, no rate limit); same pattern for prod | If DNS is not on Cloudflare: moving the whole `aindrajaya.my.id` zone (affects Vercel records) or a delegated subdomain zone (plan availability unverified); more moving parts |

### D2 - Bootstrap fix scope (gap G1)
Decided: T-008c1 as proposed (80 stage switch, `workflow_dispatch build_only`, `TRUSTED_PROXY_HOPS` parameter), plus
digest fallback in `60` and the executable bit.

---

## Gaps (found while generating this runbook)

| Id | Gap | Effect | Proposed fix |
|---|---|---|---|
| G1 | No way to push the first image: `80-wif.sh` needs the services; the services need an image; the only image path (`deploy.yml`) needs `80`; `deploy.yml` has no `workflow_dispatch` | Phase 4 blocked | Fixed in T-008c1: `WIF_STAGE=registry\|all`, `build_only` dispatch (Phases 3.4-4.5) |
| G2 | `TRUSTED_PROXY_HOPS` is a literal `1` in `60-deploy-services.sh` | Phase 6 can only change it by hand (and the next `60` run would revert it) | Fixed in T-008c1: env-file variable, default 1 |
| G3 | Scripts are committed without the executable bit (100644) while README and headers show `./NN-*.sh` | "permission denied" | Fixed in T-008c1: mode 100755 (not lib.sh, which is sourced) |
| G7 | First CI rollout placed before `65` (T-008c0 Phase 4.1 step 7): the smoke test would get 403 from the private api | red `staging` run | Fixed in T-008c1: moved to 5.1b |
| G4 | `40` assumes the Cloud Tasks service agent exists | `40` may stop at its last binding on a fresh project | Report if it happens; the fix (service identity creation) belongs in `00`/`40` |
| G5 | Checklist item 8's "duplicate delivery (503 then 200)" has no owner-runnable trigger | Not covered in Phase 8 | Covered by tests (J3); a staging check needs a scripted duplicate task (later task) |
| G6 | Task spec says anonymous ~40 MB upload; the free limit is 10 MB | Phase 8 tests the rejection anonymously and the 40 MB path as Pro | None (runbook adjusted) |

## Risks staging verifies (from registers.md and DEPLOY-PLAN)

| Id | Risk | Where verified |
|---|---|---|
| X1 / item 15 | run.app X-Forwarded-For shape undocumented | Phase 6 |
| S7 (T-008b2) | `run.developer` per resource may not cover `jobs execute --wait` / operation polling | Phase 5.1b |
| Q10 | Org policy blocking `allUsers` | Phase 0.1 (`get-ancestors`), Phase 5.1 |
| U2 / item 4 | Scheduler -> worker maintenance never observed | Phase 5.2 |
| U3 / item 7 | R2 lifecycle and uploads CORS | Phase 0.4, 7.4, 8.1 |
| Item 5 | Migrations never ran on Supabase; RLS on every table; Data API exposes only `public` | Phase 4.6 |
| Item 24 | Database secrets in bare `postgresql://` form; pooler ports | Phase 2.3 |
| G1 (registers) | Pooler client limits (staging 15 of 200) | Phase 4.4 (`/ready` probes), Phase 8 (no connection errors) |
| Deterministic URL | Worker URL = OIDC audience must match the real URL | Phase 4.4, Phase 5.2 (200, not 401) |
| ADR-004 | Worker's own OIDC check behind Cloud Run IAM | Phase 5.2, Phase 8 (no 401 on `/internal/*`) |
| Cloud Tasks agent | actAs doc conflict, agent existence | Phase 3.1 |
| J3 / J5 | Live-lease 503s retried; worker 5xx counted | Phase 8.4 (503s visible), Phase 5.3 metric |
| Item 10 / U4 | Worker memory with a large image; Pillow 179 MP ceiling | Phase 8.4 |
| Item 8 | End to end, test-mode order activates Pro once, replay is `duplicate` | Phase 8.1-8.5 |
| Q8 | Kill switch works and is reversible | Phase 8.6 |
| Item 17 | Alert policies exist (seeing each fire is a later check) | Phase 5.3 |
| UI1 | `CORS_ALLOW_CREDENTIALS` must stay true until T-008b3 | Phase 7.2 (logout works in Phase 8) |
| U11 / J4 | Exception text in `error_message` reaches clients | Phase 8 (note any raw error text in the UI) |
| S4 | OIDC certs fetched per call (latency on `/internal/*`) | Phase 8.4 latency table |
| U1 | Presign abuse accepted with bounds | not tested (accepted risk) |
| Supabase Free | Staging project pauses after 1 week idle (staging schedules are paused) | watch after bring-up; resume in the dashboard before tests |
| Re-running `60` | Older digests in the env file roll services back | `60` keeps the running images when the digests are empty (4.7, 7.2) |
| CI variables | Once set, every push to main deploys staging and requests prod approval | Phase 3.5 (no merges until 5.1b; reject prod until prod bring-up) |
| WIF `has(assertion.environment)` | Prod principalSet mapping | not validated in staging (prod job is rejected); first prod run |
