# T-DOCS-RB - Audit and reconcile STAGING-RUNBOOK.md against main@3fc950f (docs only)
Branch `docs/runbook-reconcile` from main@3fc950f (PR #23, T-008c2). Change type: docs. Rigor: Elevated (the owner
executes the runbook against real GCP). Executor: Claude Code. One local commit; the owner pushes and merges.

## Contract (summary)
Line-by-line audit of docs/engineering/deploy/STAGING-RUNBOOK.md against the merged `deploy/gcloud/*`, `env.example`
and `.github/workflows/deploy.yml`, proven with `DRY_RUN=1` runs for `ENV=staging`; Phase 2 secret names vs
`30-secrets.sh`; phase order per T-008c1; no public-outputs / DNS leftovers; explicit Cloudflare prerequisites before
Phase 2; Phase 7 vs imageopz-ui main@b86307e UI-ENVIRONMENTS.md; Phase 8 vs the UI download behavior after UI PR #7;
one "verified against" header line. Fix the runbook, update registers (UI2 done, logout and Download-all follow-ups)
and STATE, list script gaps (no script changes here).
Authorization: read this repo and ../imageopz-ui (read-only); `bash -n` and `DRY_RUN=1` runs with a failing gcloud stub.
Edit only STAGING-RUNBOOK.md, registers.md, STATE.md, this file. No gcloud mutations, no secrets, no push.

## Reconcile
- Server: `main` = `origin/main` = 3fc950f, clean; branch created from it. Scripts inspected before running them.
- UI: `../imageopz-ui` on `main` = b86307e (PR #7), clean; read only (UI-ENVIRONMENTS.md, lib/downloads.ts,
  lib/resultsStore.ts, lib/imageopzApi.ts, lib/session.ts, components/results/MyResults.tsx, src/App.tsx,
  docs/engineering/tasks/T-008c3-download-links.md). No UI command run.

## Handoff

### Phase order: one deliberate deviation from the task text
The task lists the order as "... 50/60 -> 65 -> 80 all -> migrate -> 5.1b". T-008c1's actual sequence (its handoff,
deploy/gcloud/README.md "Order", DEPLOY-PLAN section 6, and the runbook) is 50/60 -> 80 all -> migrate -> 65 -> 5.1b.
The runbook keeps T-008c1's order: `80 all` does not depend on `65`, and running `65` last means the api and payments
become public only after the schema is at head (4.4 relies on "no public traffic before 65"). The "How to use" list
now says so explicitly. If the owner wants `65` earlier, it is a one-line move, but nothing requires it.

### Change log (section -> what changed -> why / evidence)
| Section | Change | Why / evidence |
|---|---|---|
| Header | SHA/PR provenance replaced by one line "Verified against main@3fc950f (server) / main@b86307e (UI)" plus sources and evidence pointer | task item 1 (only hard-coded SHA was `main@fbe161a`; no PR numbers in the file) |
| How to use | Bootstrap order now names 3.2 (Environment `production` before any variable) and 4.2, and says why `65` follows the migration; new bullet: merge every pending PR before 3.5 | task item 1 (precondition before any variable); 3.5 already forbade merges, but the PR carrying this file must land before |
| 0.4 (new) | Cloudflare R2 prerequisites table per environment: outputs buckets `imageopz-staging` / `imageopz`, uploads buckets, one scoped token per bucket per env (4 total), uploads CORS origins per env, private-bucket checks, lifecycle, revoke the account-wide token | task item 1; DEPLOY-PLAN section 7 (CORS: `PUT, GET`, `content-type`, 3600; staging origin + localhost, prod origin only), registers U15/U3 |
| 0.5 | Remaining prerequisites (Supabase, LemonSqueezy) split out of the old 0.4 | old 0.4 said "do not create anything new", which contradicted creating buckets and tokens |
| STOP 0 | Adds the 0.4 staging checklist (no token values) | so Phase 2 does not start without the tokens |
| 2.1 | "9 secrets, 16 accessor bindings" -> "9 `secrets create`, 18 accessor bindings" | dry run: `grep -c 'secrets create'` = 9, `add-iam-policy-binding` = 18 (T-008c2 added api to both `r2-outputs-*`) |
| 2.2 | Outputs and uploads rows: full secret ids (both halves), "staging token (0.4)", consumers per token, never the prod token | names checked against `30-secrets.sh` (`secret_name` = `imageopz-<name>-staging`), consumers from its `SECRETS` list; uploads readers from `app/services/upload_store.py` |
| 2.3 | Adds the outputs-token IAM check (api and worker) | `30-secrets.sh`: `r2-outputs-secret-access-key:api,worker` |
| 3.5 | "until Phase 5.1 is done" -> "until Phase 5.1b" | 5.1b and the risks table already said 5.1b; a push between 5.1 and 5.1b would itself be the first CI rollout |
| 4.1, 5.1b | `gh run list` gains `--event=workflow_dispatch --branch=main` and a "check the created time" note | AGENTS.md Learned 2026-10-15: a bare run selector can pick another run |
| 4.4 | Expected env-block lines now include `CORS_ALLOW_ORIGINS`, `CORS_ALLOW_CREDENTIALS: 'true'` and the api's `R2_ACCESS_KEY_ID=imageopz-r2-outputs-access-key-id-staging:latest`; the stale-variable absence check removed; ingress expectation named per service | dry run of `60` with digests (excerpt below); the old "all / internal / all" did not match the name-sorted table |
| 7.1 | Rewritten to UI-ENVIRONMENTS owner steps 1-6: branch command, Vercel domain assignment, no DNS step (DNS on Vercel, `ns1/ns2.vercel-dns.com`), Preview variables scoped to `staging`, required variables, PostHog "unset or separate staging project", generic Preview values, redeploy | old step 1 pointed at "DNS record per UI-ENVIRONMENTS step 3", which now says no DNS step |
| 7.2 | `CORS_ALLOW_CREDENTIALS`: the UI no longer needs it (T-008b3 merged), but `60` writes literal `true`: stays true until gap G8 is fixed | `60-deploy-services.sh` api block `yaml_kv CORS_ALLOW_CREDENTIALS true`; UI-ENVIRONMENTS step 7 |
| 7.4 | Private-bucket check in the dashboard's own terms (Public Development URL disabled, Custom Domains empty); refers back to 0.4 | task item 1 (no public-URL wording left) |
| 8.1 | Removed the "known UI gap / reload the page" note; added UI checks: fresh `/status` per click, ids-only history (`imageopz_results_v1`), 15-min expiry from the result page and My Results, logout case with the exact message, Download all tab count | imageopz-ui `lib/downloads.ts` (`openFreshDownloads`, message text), `lib/resultsStore.ts` (key, no URL), T-008c3 handoff follow-ups |
| 8.4 | Pro download note: ownership by session id | UI calls `getJobStatus(id, { sessionId })` without the bearer; server `get_job_status` ownership branch |
| 8.6 | Adds the `resume` dry run before `DRY_RUN=0` | "every script runs first with DRY_RUN=1" rule; dry run prints 2 ingress updates, 2 queue resumes, "left paused" |
| STOP 8 | Adds the UI download checks | new checks above |
| Decisions D1 | Historical options table and DNS discussion removed; decision, mechanism and pointer to tasks/T-008c0 kept | task item 1 (no public-bucket-URL, custom-domain or Cloudflare DNS steps left) |
| Gaps | G8 (`CORS_ALLOW_CREDENTIALS` literal) and G9 (`50` requires `API_IMAGE`) added | dry runs below |
| Risks | UI1 row tied to G8; new rows O1/T-008c3, UI3/UI4, U15; CI-variables row names 3.2 | registers |

Checked and already correct (no change): every script name and path; env-file variable table vs `env.example` (same
key set as the harness env); `WIF_STAGE=registry|all` (default `all`); `build_only` input and the summary lines
`API_IMAGE='<region>-docker.pkg.dev/<project>/imageopz/api@<digest>'` / `PAYMENTS_IMAGE=...payments-ingress@...`;
`TRUSTED_PROXY_HOPS` in the env file (Phase 6 row); digests set in 4.2 and emptied in 4.7; 1.1 = 12 APIs; 3.1 queue
values; 5.2 schedules and paused state; 5.3 = 7 alert policies; repository variable names = `vars.*` in deploy.yml;
secret ids in 2.2/2.3 = the 9 in `30-secrets.sh`; alembic head `0006`; `/api/v1/uploads/presign`, `/api/v1/session/me`
and the `xff_entries=` log line exist.

### Gap list (scripts cannot do what is documented; fixes are a separate task)
| Id | Gap | Effect | Proposed fix |
|---|---|---|---|
| G8 | `60-deploy-services.sh` writes `CORS_ALLOW_CREDENTIALS` as the literal `true` | UI-ENVIRONMENTS step 7 (`false` once the header-only UI is live) cannot be applied; a manual `services update` would be reverted by the next `60` | env-file variable with default `true`, validated as true or false, like `TRUSTED_PROXY_HOPS` |
| G9 | `50-jobs.sh` requires `API_IMAGE` (T-008c1 F1). Dry run with the env file after 4.7: `error: API_IMAGE is required (see deploy/gcloud/env.example)` | documented in 4.7 (set it for that one command); a stale value re-points the job at an older image | keep-the-running-image fallback as in `60` |
| (unverified) | `gcloud monitoring policies` may be beta-only in some SDKs (already flagged in 5.3) | 5.3 stops | fix in `90` if it happens |
| (unverified) | Cloud Tasks service agent may not exist yet (G4, already in the runbook) | 3.1 stops at its last binding | already listed |

No other mismatch: every documented script step produced the documented commands.

### Dry-run evidence
Harness: gcloud and curl stubs on `PATH` (`exit 99`, each call logged); env file with every `env.example` key
(placeholder non-secret values, `PROJECT_NUMBER=123456789012`, `GITHUB_REPOSITORY_ID=1000001`, `BILLING_ACCOUNT` set),
loaded with `set -a; . <file>; set +a`; commands exactly as the runbook writes them, from the repository root,
`./deploy/gcloud/NN-*.sh` (all 13 are mode 100755; `lib.sh` 100644). `bash -n` on all 14 `deploy/gcloud/*.sh`: ok.

| Runbook step | Command (DRY_RUN=1) | Exit | `+ gcloud` lines |
|---|---|---|---|
| 1.1 | `00-enable-apis.sh` | 0 | 1 (12 APIs) |
| 1.2 | `10-artifact-registry.sh` | 0 | 2 (create, `set-cleanup-policies ... --dry-run`) |
| 1.3 | `20-service-accounts.sh` | 0 | 6 |
| 1.4 | `NOTIFICATION_CHANNEL= 90-observability.sh` | 0 | 2 (metric, budget; "skipping alert policies") |
| 2.1 | `30-secrets.sh` | 0 | 27 (9 creates, 18 bindings) |
| 3.1 | `40-queues.sh` | 0 | 9 |
| 3.4 | `WIF_STAGE=registry 80-wif.sh` | 0 | 5 |
| 4.3 | `50-jobs.sh` (digests from 4.2) | 0 | 1 |
| 4.4 | `60-deploy-services.sh` (digests) | 0 | 3 |
| 4.5 | `WIF_STAGE=all 80-wif.sh` / no `WIF_STAGE` | 0 / 0 | 13 / 13 |
| 4.6 | `EXECUTE_MIGRATE=1 50-jobs.sh` (digest still set) | 0 | 2 (deploy, `execute --wait`) |
| 5.1 | `65-service-iam.sh` | 0 | 4 |
| 5.1b | `ci-rollout.sh` (what CI runs, digests) | 0 | 5 (+5 curl smoke lines) |
| 5.2 | `70-scheduler.sh` | 0 | 4 (2 creates, 2 pauses) |
| 5.3 | `90-observability.sh` with a channel | 0 | 9 (metric, 7 policies, budget) |
| 6 | `TRUSTED_PROXY_HOPS=2 60-deploy-services.sh` (digests empty) | 0 | 3 |
| 7.2 | `60-deploy-services.sh` (digests empty) | 0 | 3 |
| 8.6 | `99-kill-switch.sh stop` / `resume` | 0 / 0 | 6 / 4 |
| G9 | `EXECUTE_MIGRATE=1 50-jobs.sh` (digests empty, after 4.7) | 1 | 0 |
| negative | `TRUSTED_PROXY_HOPS=x 60`, `API_IMAGE=foo:latest 60`, `WIF_STAGE=bogus 80` | 1, 1, 1 | 0 |

Stub calls: 0 (nothing reached gcloud or curl). Excerpts:
```
3.4  # GitHub Actions (google-github-actions/auth) inputs for staging:
     #   workload_identity_provider: projects/123456789012/locations/global/workloadIdentityPools/github/providers/imageopz-server
     #   service_account: iz-deployer-staging@imageopz-prod-4821.iam.gserviceaccount.com
4.4  # imageopz-worker-staging: image from API_IMAGE
     # imageopz-api-staging: image from API_IMAGE
     # imageopz-payments-staging: image from PAYMENTS_IMAGE
     TRUSTED_PROXY_HOPS: '1'
     CORS_ALLOW_ORIGINS: 'https://staging.imageopz.aindrajaya.my.id'
     CORS_ALLOW_CREDENTIALS: 'true'
     R2_BUCKET_NAME: 'imageopz-staging'
     + gcloud run deploy imageopz-api-staging ... --set-secrets=...,R2_ACCESS_KEY_ID=imageopz-r2-outputs-access-key-id-staging:latest\,R2_SECRET_ACCESS_KEY=imageopz-r2-outputs-secret-access-key-staging:latest\,R2_UPLOADS_ACCESS_KEY_ID=...
7.2  # dry-run: API_IMAGE unset, would keep the image imageopz-api-staging runs now (describe; error if imageopz-api-staging does not exist)
     --image=CURRENT_IMAGE_OF_imageopz-api-staging
6    TRUSTED_PROXY_HOPS: '2'
8.6  # Scheduler jobs left paused (PAUSE=1); PAUSE=0 resumes them
G9   error: API_IMAGE is required (see deploy/gcloud/env.example)
neg  error: TRUSTED_PROXY_HOPS must be a non-negative integer
     error: API_IMAGE must be pinned by digest (...@sha256:...)
     error: WIF_STAGE must be registry or all
```
Text checks on the result: no `R2_CUSTOM_DOMAIN`, `r2.dev`, CNAME, Cloudflare DNS or em-dash in the runbook; the only
SHAs are the header line's.

### Not verified
- Anything on GCP, GitHub, Cloudflare, Vercel or Supabase (out of authorization): the `gh` commands of Phases 3-5 were
  checked against `deploy.yml` only; dashboard labels in 0.4 and 7.1 come from UI-ENVIRONMENTS and DEPLOY-PLAN.
- `DRY_RUN=0` paths (describe fallbacks, preflight die) as in T-008c1.
- The UI download behavior in a browser (Phase 8.1 is the first check).

### Follow-ups (recorded, not done)
- Script task: G8, G9 (above).
- registers UI3 (send the bearer token on `/status`; UI task before prod), UI4 (Download all; later UX).
- STATE "Owner items" still says "r2.dev disabled" in its D1 line; harmless wording, left as is.

### Owner commands
```sh
git log -1 --stat docs/runbook-reconcile
git diff main...docs/runbook-reconcile
git push -u origin docs/runbook-reconcile
gh pr create --base main --head docs/runbook-reconcile --title "docs(deploy): T-DOCS-RB reconcile STAGING-RUNBOOK with main@3fc950f and UI main@b86307e" --body-file docs/engineering/tasks/T-DOCS-RB-runbook-reconcile.md
gh pr checks --watch && gh pr merge --squash --delete-branch
```
Then STAGING-RUNBOOK Phase 0 (including 0.4 in Cloudflare), STOP after each phase.
