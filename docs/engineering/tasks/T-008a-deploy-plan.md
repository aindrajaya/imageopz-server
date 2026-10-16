# T-008a - Deploy plan and reviewable gcloud scripts (staging + prod), no resources created
Branch `docs/deploy-plan` from main@b595faa. Change type: docs + scripts. Rigor: Elevated (cost, IAM, secrets).
Executor: Claude Code. imageopz-ui read at main@f6f19ce (read-only). One local commit; the owner pushes and merges.

## Handoff
Deliverables:
- `docs/engineering/deploy/DEPLOY-PLAN.md`: architecture, resource inventory, route exposure, IAM matrix, secrets
  and config per environment, G1 pool math, image strategy, networking/domains, A1, observability, cost, go-live
  checklist, rollback runbook, open questions Q1-Q11, 44 cited doc references.
- `deploy/gcloud/`: `lib.sh` and numbered scripts 00, 10, 20, 30, 40, 50, 60, 65, 70, 80, 90; `ar-cleanup-policy.json`,
  `env.example` (non-secret parameters only), `README.md`.
- Records: STATE.md (T-007 PR #17 rebase merge e140e71, 9b1de12, e0793da, b595faa; UI T-006d PR #5 f6f19ce; the svg fix
  is imageopz-ui PR #4 49e0c95, from the UI git log), registers.md (H1, C1, D1; U2 note; T-008 checklist pointer).

No GCP resource was created or changed, no API enabled, no IAM or secret touched, no script run with DRY_RUN=0.

### Summary of the plan
- One project, `-staging`/`-prod` suffixed resources, per-environment identities, secrets, queues and jobs; shared
  Artifact Registry (promote one digest). Trade-off and revisit triggers in section 1.
- Services: api (public, no `/internal`), worker (`/internal/*` only, `ingress=internal`, private, concurrency 1,
  timeout 1800 s), payments ingress (public). Both queues target the worker's run.app URL (required for internal
  ingress with Cloud Tasks) with one invoker identity per environment.
- Jobs queue: 15 attempts / 7200 s / 30-600 s backoff (passes the 1800 s lease at the 7th retry, ends ~17 attempts).
  Payments queue: 50 attempts / 24 h.
- IAM: every grant resource-scoped; no project-level roles for workloads or CI; CI via WIF impersonation (Cloud Run
  does not support direct federation), numeric repo/owner ids, `refs/heads/main`, prod deployer only from the GitHub
  Environment `production`.
- Cost: ~$1-5/month. Main finding (D1): Cloud Run Jobs bill >= 1 min per execution, so the registers' 5-minute sweep
  would cost up to ~$9.9 per environment per month. Scripts use 15 min / 6 h; Q1 recommends moving sweep and cleanup
  to Scheduler -> worker HTTP endpoints.
- Go-live blockers include the missing Python Dockerfile and CI workflow (T-008b), U15, queues, scheduling, migrate,
  secrets, R2 CORS + lifecycle, staging end-to-end, the LemonSqueezy URL switch, the worker memory measurement and a
  manual Supabase dump (Free plan has no backups).

### New findings from reading the code (logged)
- H1 `/health` returns 200 with exception text on DB errors.
- C1 session cookie `secure` only in production.
- D1 scheduled-job billing (above).
- No Python Dockerfile in the repo (STATE said "docker build only proven in CI": that is the Go image).
- `RESEND_API_KEY` has no caller (`app/services/email_service.py` is unused), so it is not provisioned.
- `ENVIRONMENT=production` makes every Python process (worker, CLIs, Alembic) require SECRET_KEY; T-008b should
  scope the check to the api.

## Checks
| Check | Result |
|---|---|
| `bash -n` (macOS /bin/bash 3.2.57) on lib.sh and all 11 scripts | all pass |
| shellcheck | not installed on this machine (`which shellcheck`: not found); not run |
| Dry run of every script with placeholder values (staging and prod), before and after the review fixes | all exit 0; argument guards tested (ENV=dev rejected, missing params rejected, image without digest rejected) |
| `env.example` sourced with `set -a; . ./env.example` | parses (values quoted; cron strings and `<placeholders>` no longer break sourcing) |
| Em-dashes in new/changed docs | none (searched for U+2014) |
| Secret values | none in any file; scripts only reference secret names; values are added via `--data-file=-` |
| Every cited limit/price has a URL | yes, [R1]-[R44] in DEPLOY-PLAN.md; pricing pages that render client-side are marked "(snippet)" |

### Read-only gcloud calls
None succeeded. `gcloud config list`, `gcloud auth list` and `gcloud projects describe imageopz-prod-4821` all fail
at start-up: gcloud cannot read `~/.config/gcloud/active_config` ("Operation not permitted", macOS file access for this
process, also outside the sandbox). I did not change permissions. Consequences: the project number, enabled APIs, org
policy (Q10) and existing resources are unverified; the scripts take `PROJECT_NUMBER` or read it at run time.
`gcloud ... --help` was replaced by the public gcloud reference pages (cited).

### Network used
WebSearch/WebFetch of public docs only: docs.cloud.google.com / cloud.google.com, supabase.com, developers.cloudflare.com,
github.com/google-github-actions/auth (three research subagents and my own checks). No GCP API call, no GitHub API call.

## Independent review (subagent, fresh context, read-only; verbatim)
## Independent review: DEPLOY-PLAN.md and deploy/gcloud (T-008a)

**Verdict: no MUST findings.** I found 7 SHOULD and 9 NIT items. The scripts look correct, and the cost and J3 backoff arithmetic check out. The weak spots are:
- One wrong sentence in the J3 rationale.
- Alerting that probably never fires.
- A DATABASE_URL format trap for the Go service.
- How the sweeper behaves when the queue has a backlog.
- Some deployer and WIF scoping.

**What I ran:**
- `/bin/bash -n` (bash 3.2.57) passes on every script. There are no bash-4 features and no em-dashes in the plan, scripts or registers.
- The `60-deploy-services.sh` dry run for staging ran. It printed commands and env files only. All secrets go through `--set-secrets`, and no secret values appear anywhere.
- The prod dry runs, and the dry runs of the other scripts, were blocked by the permission classifier ("Production Deploy"). I reviewed them by reading the code.

### Findings

**1. SHOULD - DEPLOY-PLAN.md:108-109: the J3 rationale is wrong.**
- **Problem:** The plan says retries end "well before the sweeper's FAIL of PROCESSING jobs at 2x the lease (3,600 s)". In fact they end at about 7,530 s, after that threshold.
  - Backoff with min 30 s, max 600 s and 5 doublings gives gaps of 30, 60, 120, 240, 480, then 600 s each. This matches the RetryConfig doc example, which I checked.
  - Cumulative retry times are 30, 90, 210, 450, 930, 1530, 2130 s, and so on.
  - The 16th attempt lands at 6,930 s, which is still under 7,200 s. So a 17th attempt runs at about 7,530 s, then retries stop.
- **Effect:** The behaviour is harmless, because a delivery to a job already FAILED returns 200 and ends the chain. But the rationale for the key J3 requirement is false.
- **Fix:** Say that retries outlast the 2x-lease FAIL, and that a late delivery to a FAILED job is a 200 no-op.
- The config itself meets J3:
  - The 7th retry comes about 2,130 s after the first failure, which is past the 1,800 s lease.
  - Attempts are bounded.
  - The worst case still works when the first attempt runs to the 1,800 s dispatch deadline, because maxRetryDuration counts from the first attempt.
  - Worker timeout 1800 >= dispatch deadline 1800 = lease 1800, as ADR-005 requires.

**2. SHOULD - DEPLOY-PLAN.md:102,114 and lib.sh:114: a queue backlog gets duplicated, then FAILED.**
- **Problem:** Jobs-queue concurrency is 1 in staging and 2 in prod. Every job waiting in the Cloud Tasks backlog stays PENDING with its original `enqueued_at`.
  - With a 15-minute sweep and a 600 s stale threshold, the sweeper adds `-r1`, `-r2`, `-r3` duplicates at about 10-25, 25-40 and 40-55 minutes.
  - At about 55-70 minutes it FAILs the job ("not queued"), and the credit stays spent (J1).
- **Scenario:** A Pro bulk upload of 30 large images at about 2 minutes each, at concurrency 1, takes about 60 minutes. The tail jobs get FAILED, and the duplicates make the backlog longer.
- **Fix:** Record the maximum tolerated backlog (about 1 hour) in the plan. Then do one of:
  - Raise JOB_STALE_PENDING_SECONDS for the sweep job to 1800-3600.
  - Size concurrency or max-instances so the worst-case bulk drains within it.
- Also make the queue-depth alert (90-observability.sh:74-87) fire before the FAIL horizon. Today it fires at depth > 20 for 30 minutes.

**3. SHOULD - 90-observability.sh:53-56 and DEPLOY-PLAN.md:301-302: the ERROR alerts probably never match (UNVERIFIED).**
- **Problem:** The app logs plain text to stderr through `logging.basicConfig`. The Cloud Run logging docs I fetched don't say what severity plain-text lines get.
  - As far as I know, Cloud Run gives them DEFAULT severity, not ERROR. In that case `severity>=ERROR` never matches.
  - Tracebacks are also split into one entry per line.
- **Scenario:** J5's catch-all 503 for bugs, and failures of the sweep or cleanup jobs, produce no alert. That also means the T-007 "alert on non-zero exit" item is not really covered.
- **Fix:** Pick one:
  - Emit JSON structured logs with a `severity` field (a T-008b code change).
  - Filter on `textPayload=~" - ERROR - "`.
  - For jobs, alert on the Cloud Run job execution or task failure system log rather than app severity.
- Verify in staging as part of checklist item 17.

**4. SHOULD - DEPLOY-PLAN.md:172 and 30-secrets.sh:15: the shared DATABASE_URL secret has no stated format.**
- **Problem:** The plan says "Go accepts the same URL". Go's `pgxpool.ParseConfig` rejects `postgresql+psycopg://`, which is the form AGENTS.md uses for TEST_DATABASE_URL.
- **Scenario:** The owner pastes the psycopg form. The payments ingress then crash-loops at startup, and checkout is broken.
- **Fix:** State that the secret must use bare `postgresql://` (or `postgres://`), with the pooler user `postgres.<ref>`. Python maps that form to psycopg and disables prepared statements (`app/models.py:273-277`; `app/config.py:143-149`).

**5. SHOULD - 80-wif.sh:36 and DEPLOY-PLAN.md:155-160: the WIF condition does not pin the workflow.**
- **Problem:** The condition checks only repo id, owner id and `ref=='refs/heads/main'`.
- **Scenario:** Someone later adds a workflow triggered by `pull_request_target` or `workflow_run`. That workflow runs with ref = main, so a fork PR could get a main-ref token and impersonate `iz-deployer-staging`.
- **Fix:** Add `assertion.job_workflow_ref` (or `workflow_ref`) == `<owner>/<repo>/.github/workflows/deploy.yml@refs/heads/main`. Alternatively restrict `assertion.event_name` to push and workflow_dispatch.
- Related NIT: the prod principalSet `attribute.environment/production` (80-wif.sh:58) admits any provider in the generic `github` pool. Map a composite attribute such as `repository_id + '/' + environment`, or use a dedicated pool.
- UNVERIFIED: whether the `has(assertion.environment) ? ... : 'none'` mapping (80-wif.sh:35) is accepted. Test it with both an environment job and a plain job.

**6. SHOULD - DEPLOY-PLAN.md:164-165 and 379, and 80-wif.sh:73-76: CI can run downgrades.**
- **Problem:** The plan says "Only the owner runs downgrades". But `roles/run.developer` (checked on the IAM roles page) includes:
  - `run.jobs.runWithOverrides`, `run.jobs.delete` and `run.services.delete`
  - `run.instances.sshRoot`
- The script grants this role to the deployer on the migrate job.
- **Scenario:** A compromised workflow on main runs `alembic downgrade base` on prod. Supabase Free has no backups.
- **Fix:** Either accept this explicitly and remove the claim, or give CI a custom role on the jobs:
  - `run.jobs.get`, `run.jobs.update`, `run.jobs.run`
  - `run.executions.get`, `run.executions.list`, `run.operations.get`
- The open risk about operation polling (plan line 161) also applies to `jobs execute --wait`, which needs `run.executions.get`. Add it to that note.

**7. SHOULD - DEPLOY-PLAN.md:340-363: go-live coverage gaps.**
- **Covered:**
  - The registers T-008 checklist; U1, U2, U3, U15, A1, G1, S4, J5, H1, C1, D1; J2 deferred.
  - The T-007 "T-008 list": queue, service accounts, OIDC env, worker as its own service, timeout, concurrency, memory, `create_task` timeout, scheduler.
  - Sweep and cleanup scheduling is BLOCKER #4.
- **Missing:**
  - (a) The T-005 item "consider retry/idle-in-transaction limits on the Supabase pooler", e.g. an `idle_in_transaction_session_timeout` on the owner role. It is neither listed nor deferred.
  - (b) ADR-002 validation, "billing report after first month".
  - (c) A checklist line for the alert on non-zero job exit (see finding 3).
- **Fix:** Add these as non-blocking items, or defer them explicitly.

**8. NIT - 40-queues.sh:16 vs JOB_STALE_PENDING_SECONDS=600.**
- max-backoff 600 s equals the stale threshold. A transient-failure retry fires about 600 s after the worker reset `enqueued_at`, so a sweep landing in that gap adds a duplicate `-rN` and uses up a re-enqueue.
- Use max-backoff 540 s, or a stale threshold above 600 s.

**9. NIT - DEPLOY-PLAN.md:138 and 40-queues.sh:50: one OIDC identity for both routes.**
- api and sweep can actAs `iz-tasks-invoker`, so they could create a jobs-queue task targeting `/internal/payments/events` with an arbitrary body and grant Pro.
- For api this adds little, because it already holds the LemonSqueezy signing secret. For sweep it is a real privilege gain.
- Optional fix: separate invoker identities per route, with a per-route allowed-email check in the app.

**10. NIT - 90-observability.sh:103-108: budgets create is not idempotent.**
- Running the script with BILLING_ACCOUNT set in both env files creates duplicate budgets.
- Guard it with `gcloud billing budgets list --filter=displayName`, or document that BILLING_ACCOUNT is set only for the prod run.

**11. NIT - 70-scheduler.sh:37-39: no resume path.**
- On an existing paused job, PAUSE=0 does not resume it, so promoting a staging schedule or re-running for prod after a manual pause leaves it paused.
- Add `else gcloud scheduler jobs resume`.

**12. NIT - 40-queues.sh:58: the Cloud Tasks service agent may not exist yet.**
- The binding fails if the agent hasn't been provisioned.
- Add `gcloud beta services identity create --service=cloudtasks.googleapis.com` in 00 (UNVERIFIED whether it is needed).

**13. NIT - DEPLOY-PLAN.md:243-244: "minus IAM" should name the flag.**
- Say that CI must also drop `--no-allow-unauthenticated` from the worker deploy. It may trigger an IAM policy update that `run.developer` cannot perform (UNVERIFIED).

**14. NIT - Cloud Run might strip the token signature (UNVERIFIED).**
- The docs say Cloud Run removes the token signature only for `X-Serverless-Authorization`. If it also did this for `Authorization`, the worker's own token check (`internal_auth.py:54`) would return 401 for everything.
- Add "the worker's own OIDC check passes behind IAM" explicitly to checklist item 8.

**15. NIT - ar-cleanup-policy.json:13-15: prod images are kept forever.**
- `prod-*` tags are kept indefinitely, so storage grows without limit (small: about $0.10/GiB-month). Revisit later.

**16. NIT - DEPLOY-PLAN.md:41 and Q7.**
- Note that "2 active free projects" is per organization, so an existing free project the owner uses elsewhere would block a staging project.

### Checked and OK
- **Ingress:** internal ingress admits Cloud Tasks and Scheduler from the same project on the run.app URL (doc quoted).
- **Scheduler:** OAuth to run.googleapis.com is correct. `run.invoker` includes `run.jobs.run` (verified).
- **actAs:** granted to each enqueuer on the invoker SA.
- **Secrets:** per-secret bindings match each process's settings usage. I grepped every `settings.*` use per module: api does not need outputs creds, the worker's payment path needs nothing extra, and SECRET_KEY is required everywhere by the production validator, as the plan says.
- **Go service:** env vars match `config.go`.
- **G1:** pooler arithmetic is correct (31 and 19 clients).
- **Cost arithmetic:** the figures below, and the ~$1.2 to ~$4.6 total, are correct.
  - 2,880 x 60 = 172,800 vCPU-s gives about $3.42.
  - 8,640 x 60 = 518,400 vCPU-s gives about $9.85.
  - Secret Manager: 12 versions x $0.06.
  - Scheduler: 4 jobs = $0.10.
- **Cost guards** are present: max-instances, min 0, bounded queue retries, scheduler retries 0, job retries and timeouts, AR cleanup, budget.

### Verdict per lens
| Lens | Verdict |
|---|---|
| 1 Least privilege | Partially met: resource-scoped throughout, no project roles, no keys. Gaps: deployer's run.developer (overrides, delete), WIF has no workflow pin, shared invoker identity. |
| 2 Cost guards | Met: arithmetic verified; budget create not idempotent (NIT). |
| 3 J3 retry | Config met; rationale sentence wrong (1); backlog vs stale-PENDING FAIL (2) and backoff = stale threshold (8) not addressed. |
| 4 Go-live blockers | Partially met: all T-008 items and sweep/cleanup scheduling covered. Missing: pooler idle-in-transaction (T-005), month-one billing report (ADR-002), an effective non-zero-exit alert (3), DATABASE_URL format (4). |
| Secrets, DRY_RUN, idempotency, bash 3.2, no em-dashes | Met (except the budget idempotency NIT). |

### Disposition
No MUST findings, so nothing was mandatory. All SHOULDs were fixed anyway; the line numbers above refer to the
reviewed version.
| # | Action |
|---|---|
| 1 | Fixed: the plan states the real end (~7,530 s), that retries outlast the 2x-lease FAIL, and that a delivery to a FAILED job is a 2xx no-op. |
| 2 | Fixed: the sweep job runs with `JOB_STALE_PENDING_SECONDS=3600` (tolerates ~1 h of backlog; a lost enqueue is recovered after ~1 h instead of ~10 min, recorded). The queue-depth alert is now > 10 for 15 min. The env table is updated. |
| 3 | Fixed: the filters match `severity>=ERROR OR textPayload=~" - ERROR - "`; checklist item 17 requires seeing each policy fire in staging; structured logs are a T-008b option. |
| 4 | Fixed: section 5 and `30-secrets.sh` require the bare `postgresql://` form; checklist item 24. |
| 5 | Fixed: the WIF condition pins `assertion.workflow_ref` (`DEPLOY_WORKFLOW`, default `.github/workflows/deploy.yml@refs/heads/main`); the prod principalSet uses the composite `attribute.repo_env = <repository_id>:<environment>`; validating the `has()` mapping is noted for the first CI run. |
| 6 | Accepted explicitly: the plan now states that run.developer includes runWithOverrides/delete, the mitigations, and an optional custom-role hardening for T-008b. The operation-polling risk now covers `jobs execute --wait`. |
| 7 | Fixed: checklist items 22 (Supabase idle-in-transaction and statement timeouts), 23 (first-month billing report), 17 (non-zero-exit alert seen firing). |
| 8 | Fixed by 2 (3600 > 600 s max backoff). |
| 9 | Logged in plan section 4 as a follow-up (per-route invoker identities). |
| 10 | Fixed: the budget is created only when no budget with the same display name exists. |
| 11 | Fixed: PAUSE=0 resumes an existing job. |
| 12 | Not changed: the service agent is created when the API is enabled ([R19] tasks access-control doc). If the binding fails, run `gcloud beta services identity create --service=cloudtasks.googleapis.com` (noted here). |
| 13 | Fixed: section 6 names the flags CI must omit. |
| 14 | Fixed: checklist item 8 includes "the worker's own OIDC check passes behind Cloud Run IAM". |
| 15 | Accepted (small cost); revisit with the first-month billing report (item 23). |
| 16 | Fixed: Q7 notes that the limit counts existing free projects. |
Re-checks after the fixes: `bash -n` on every script, a full dry run of all scripts for staging and prod, and the
em-dash search, all clean.

## Follow-ups
T-008b (next): Dockerfile; CI workflow (build once, digest, migrate, staging, gated prod promotion); `SERVICE_ROLE`;
`EXPOSE_API_DOCS` (A1); S4; H1; C1; SECRET_KEY check scope; `create_task` timeout; XFF verification; Q1 if accepted.
Owner: answer Q1-Q11; U15; Supabase staging project; R2 staging buckets, CORS and lifecycle; Vercel staging domain.

## Owner commands (not run)
git push -u origin docs/deploy-plan
gh pr create --fill --base main
gh pr checks <n> --watch && gh pr merge <n> --squash --delete-branch
