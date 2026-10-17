# T-008c0 - Staging runbook (owner-executed), generated from the merged scripts
Branch `docs/staging-runbook` from main@fbe161a (T-008b2, PR #20). Change type: docs. Rigor: Elevated (first real cloud
changes). Executor: Claude Code. One local commit; the owner pushes and merges.

## Contract (summary)
Write docs/engineering/deploy/STAGING-RUNBOOK.md for the owner, phases 0-8 with a STOP after each; present (not decide)
how staging serves output images; list the register risks staging verifies; report gaps as follow-ups instead of
improvising. Authorization: read the repo and imageopz-ui UI-ENVIRONMENTS.md; `bash -n` and `DRY_RUN=1` runs for
`ENV=staging` only; no gcloud call that creates or modifies anything, no `DRY_RUN=0`, no secrets.

## Reconcile
HEAD fbe161a = STATE's next step (T-008b2 merged as #20). Read: deploy/gcloud/* (all scripts, lib.sh, README,
env.example), deploy.yml, image.yml, Dockerfile, DEPLOY-PLAN.md (sections 0-13), registers.md, STATE.md,
../imageopz-ui/docs/engineering/deploy/UI-ENVIRONMENTS.md, app code for `/ready`, `/version`, `xff_entries`, `r2_url`,
tier limits, Go webhook routes.

## Handoff
Files: docs/engineering/deploy/STAGING-RUNBOOK.md (new), this file, STATE.md (snapshot). No code or script changed.

### Checks run
- `bash -n` on all 14 files in deploy/gcloud: ok.
- `DRY_RUN=1` for staging, in order 00, 10, 20, 30, 40, 50 (`EXECUTE_MIGRATE=1`), 60, 65, 70, 80, 90 (with a placeholder
  `NOTIFICATION_CHANNEL`), 99 stop, 99 resume, ci-rollout. Placeholder env (no secrets; `PROJECT_NUMBER` unset, so URLs
  print `PROJECT_NUMBER`). Exit 0, 97 printed commands, no `describe` executed (lib.sh `exists` returns absent in dry
  run). Run as `bash ./NN-*.sh`: `./NN-*.sh` fails with "permission denied" (files are 100644, gap G3).

### DRY_RUN=1 transcript excerpts (staging)
```
### Secrets (containers only, no values)
+ gcloud secrets create imageopz-database-url-staging --project=imageopz-prod-4821 --replication-policy=user-managed --locations=asia-northeast1 --labels=app=imageopz\,env=staging
  (9 secrets, 16 `secrets add-iam-policy-binding` lines)
### Queues
+ gcloud tasks queues create imageopz-jobs-staging --project=imageopz-prod-4821 --location=asia-northeast1 --max-attempts=15 --max-retry-duration=7200s --min-backoff=30s --max-backoff=600s --max-doublings=5 ...
### Execute imageopz-migrate-staging and wait
+ gcloud run jobs execute imageopz-migrate-staging --project=imageopz-prod-4821 --region=asia-northeast1 --wait
### Deploy imageopz-worker-staging
SERVICE_ROLE: 'worker'
INTERNAL_AUTH_MODE: 'oidc'
INTERNAL_AUTH_AUDIENCE: 'https://imageopz-worker-staging-PROJECT_NUMBER.asia-northeast1.run.app'
INTERNAL_AUTH_ALLOWED_EMAILS: 'iz-tasks-invoker-staging@imageopz-prod-4821.iam.gserviceaccount.com,iz-scheduler-staging@imageopz-prod-4821.iam.gserviceaccount.com'
+ gcloud run deploy imageopz-worker-staging ... --image=asia-northeast1-docker.pkg.dev/imageopz-prod-4821/imageopz/api@sha256:DIGEST --service-account=iz-worker-staging@... --ingress=internal --no-allow-unauthenticated ...
### Deploy imageopz-api-staging
TRUSTED_PROXY_HOPS: '1'
### Service invoker bindings
+ gcloud run services add-iam-policy-binding imageopz-api-staging --project=imageopz-prod-4821 --region=asia-northeast1 --member=allUsers --role=roles/run.invoker
### Scheduler jobs -> imageopz-worker-staging (pause=1)
+ gcloud scheduler jobs create http imageopz-sweep-uploads-staging ... --uri=https://imageopz-worker-staging-PROJECT_NUMBER.asia-northeast1.run.app/internal/maintenance/sweep ...
+ gcloud scheduler jobs pause imageopz-sweep-uploads-staging --project=imageopz-prod-4821 --location=asia-northeast1
### Workload Identity pool and GitHub provider
+ gcloud iam workload-identity-pools providers create-oidc imageopz-server --project=imageopz-prod-4821 --location=global --workload-identity-pool=github ...
### Alert policies: 7 x `gcloud monitoring policies create`
### Kill switch: STOP
+ gcloud run services update imageopz-api-staging --project=imageopz-prod-4821 --region=asia-northeast1 --ingress=internal --quiet
+ gcloud run services update imageopz-payments-staging --project=imageopz-prod-4821 --region=asia-northeast1 --ingress=internal --quiet
+ gcloud tasks queues pause imageopz-jobs-staging ...      + gcloud tasks queues pause imageopz-payments-staging ...
+ gcloud scheduler jobs pause imageopz-sweep-uploads-staging ...   + ... imageopz-cleanup-expired-staging ...
### Kill switch: RESUME (schedules paused=1)
  ingress all on api and payments, both queues resumed, "# Scheduler jobs left paused (PAUSE=1)"
### Smoke test (ci-rollout)
+ curl -fsS --retry 5 ... https://imageopz-api-staging-PROJECT_NUMBER.asia-northeast1.run.app/{health,ready,version}
+ curl -fsS --retry 5 ... https://imageopz-payments-staging-PROJECT_NUMBER.asia-northeast1.run.app/{health,ready}
```

### Gaps (detail in the runbook, section "Gaps")
- G1 (blocks Phase 4): no path to push the first image. `80-wif.sh` binds run.developer on services and the migrate job
  that do not exist yet; the services need an image; the only build path is `deploy.yml`, which needs `80`; and
  `deploy.yml` has no `workflow_dispatch` (the contract assumed one). Proposed follow-up **T-008c1**: (1) `80-wif.sh`
  `WIF_STAGE=registry|all`; (2) `deploy.yml` `workflow_dispatch` with `build_only`; (3) `TRUSTED_PROXY_HOPS`
  parameter in `60` (G2).
- G2: `TRUSTED_PROXY_HOPS` is a literal in `60`.
- G3: scripts lack the executable bit (runbook uses `bash`).
- G4: `40` assumes the Cloud Tasks service agent exists.
- G5: no owner-runnable duplicate-delivery trigger for checklist item 8.
- G6: contract asked for an anonymous ~40 MB upload; the free limit is 10 MB. The runbook tests the anonymous rejection
  and the 40 MB path as Pro after the test purchase.

### Contradiction for the owner
The contract says DNS for the domain is on Vercel; UI-ENVIRONMENTS.md step 3 says the staging CNAME goes into
Cloudflare DNS for `aindrajaya.my.id`. Decision D1 depends on which is true (`dig +short NS aindrajaya.my.id`).

### Unverified in the runbook (stated there)
- Expected HTTP code for internet requests to an `ingress=internal` service (written as 404 from Google's front end).
- `gcloud scheduler jobs run` on a paused job (runbook resumes, runs, pauses instead).
- `gcloud monitoring policies` / `channels` availability outside beta in the owner's SDK.
- LemonSqueezy signing-secret length limit (runbook generates 40 hex chars).

### Owner commands
```sh
git push -u origin docs/staging-runbook
gh pr create --repo aindrajaya/imageopz-server --base main --head docs/staging-runbook \
  --title "T-008c0 staging runbook (owner-executed)" --body-file docs/engineering/tasks/T-008c0-staging-runbook.md
gh pr checks <n> --watch && gh pr merge <n> --squash --delete-branch
```
Merging is a push to main: the deploy workflow stays dormant because no repository variable exists yet (set only in
runbook Phase 4.1 step 2).
