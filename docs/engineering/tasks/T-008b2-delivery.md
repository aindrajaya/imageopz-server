# T-008b2 - Delivery: /ready rename, client-IP trust, Python image, CI build, deploy workflow (dormant), scripts sync
Branch `feat/delivery` from main@3cd8057 (T-008b1, PR #19). Change type: delivery + fixes. Rigor: Elevated (supply
chain, IAM, client identity). Executor: Claude Code. Local commits only; the owner pushes and merges.

## Contract (summary)
0 records (T-008b1 merged as #19 / 3cd8057; Q5 final); 1 `/readyz` -> `/ready`; 2 client IP via TRUSTED_PROXY_HOPS
(test-first); 3 Dockerfile + .dockerignore; 4 PR CI job `image`; 5 dormant deploy workflow (WIF, digest promotion,
`production` environment, concurrency); 6 scripts synced with DEPLOY-PLAN Q1-Q11, kill switch; 7 checks and an
independent review. Stop and propose a split above ~700 non-test lines.

## Reconcile
HEAD 3cd8057 = STATE baseline after the T-008b1 merge. SQLite 171 passed / 13 skipped; Postgres 16.15 (Homebrew,
throwaway cluster on 5433) 184 passed: the T-008b1 amendment numbers.

## Handoff
Commits (branch, local): 3faacc6 app + image + CI + Go rename, 8f427df scripts, 42d01ab docs, then one review-fix
commit and one records commit (this file, STATE.md).

### 1. /ready
Python already served `/ready` (T-008b1 amendment, in 3cd8057). Done here: the Go ingress (`/health`, `/ready`;
handler `ready`; its test pins `/healthz` and `/readyz` as 404; `scripts/e2e-direct.sh`; README), startup probes in
`60` on `/ready`, DEPLOY-PLAN. Basis: https://docs.cloud.google.com/run/docs/known-issues "Reserved URL paths":
"Some paths ending with z. To prevent conflicts with reserved paths, we recommend avoiding all paths that end in z".
OpenAPI regenerated: no diff (no route change).

### 2. Client IP
Docs: the X-Forwarded-For shape of a direct run.app request is NOT documented (container contract and known issues
say nothing). Documented: Google's external load balancer appends, "X-Forwarded-For: [<supplied-value>,]<client-ip>,
<load-balancer-ip>", and "does not verify any IP addresses that precede <client-ip>,<load-balancer-ip>"
(https://docs.cloud.google.com/load-balancing/docs/https). So the contract's "default matching Cloud Run per the
docs" cannot be met from the docs alone. Decision: `TRUSTED_PROXY_HOPS` default 1 (rightmost entry). Under either
shape it never selects a client-supplied entry; worst case (if run.app also appends a front-end address) every
client hashes to a Google address, which today only mislabels `ip_hash` metadata (not a durable identity,
`app/routes/session.py`). Verification: the api logs `xff_entries=<n>` (a count, no addresses) on the key paths; in
staging a request without a client XFF showing 1 confirms the default, 2 means set `TRUSTED_PROXY_HOPS=2`.
- `app/config.py`: `trusted_proxy_hops: int = Field(1, ge=0)`.
- `app/utils/security.py`: `IpFingerprint.client_ip` joins all XFF header lines, drops empty entries, takes the
  hops-th from the right (leftmost if fewer); no header or 0 -> socket peer. `get_ip_hash` uses it.
- uvicorn: `--proxy-headers` with the default `--forwarded-allow-ips` (127.0.0.1). uvicorn 0.30.6 with `*` takes the
  FIRST entry (`ProxyHeadersMiddleware.get_trusted_client_host`); the app reads the raw header for hops > 0, so the
  uvicorn setting cannot reintroduce the spoof.
- Tests `tests/test_client_ip.py` (10, via `GET /api/v1/session/me`): failing first (10 failed before the fix; a
  direct probe showed the spoofed leading entry won), 10 passed after.

### 3. Image
`Dockerfile`: builder and runtime both `python:3.11.17-slim-trixie@sha256:e88e9763...f6ce` (= `3.11-slim`, Docker
Hub index digest, last updated 2026-10-09); venv `/opt/venv` built in the builder and copied; runtime installs only
`libmagic1t64` (Debian trixie has no package named `libmagic1`; `file` 1:5.46-5, packages.debian.org); user/group
10001; `IMAGEOPZ_ENV_FILE=""`; `PORT=8080`; CMD refuses to start unless `SERVICE_ROLE` is `api` or `worker` (exit 64),
then `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers`. The migrate job overrides the
command (`alembic upgrade head`). No HEALTHCHECK. `.dockerignore`: `.env*`, `*.db*`, `.git`, tests, docs, services,
node_modules, `__pycache__`, and other non-runtime trees. Not built locally (no Docker); CI builds it.

### 4. CI `image` (PR)
`.github/workflows/image.yml`, job `image`: buildx build with `load: true`, no push, provenance/sbom off; asserts
non-root, no gcc/cc/g++, python-magic sniffs PNG, `alembic heads`, exit 64 without `SERVICE_ROLE`; api role
(`DATABASE_URL=sqlite:////tmp/ci.db`, dummy SECRET_KEY): `/health` 200, `/ready` 200, `/internal/jobs/process` and
`/internal/maintenance/sweep` 404; worker role: `/internal/jobs/process` 401, `/api/v1/session/me` 404.
Required check: recommended YES once it has passed on this PR. It is not path-filtered, runs in about 2-4 minutes,
and is the only check that proves the image builds and starts. Add the check name `image` to ruleset 25125469 only
after seeing that exact name in `gh pr checks` (AGENTS.md lesson).

### 5. Deploy workflow (dormant)
`.github/workflows/deploy.yml`: on push to main; every job `if: vars.GCP_WIF_PROVIDER != ''`; workflow
`permissions: contents: read`, `id-token: write` only on build/staging/production; `concurrency: deploy`,
`cancel-in-progress: false`. Jobs: `tests` (reusable `backend-tests.yml`, now with `workflow_call`) -> `build`
(auth as the staging deployer, `token_format: access_token`, docker/login-action with `oauth2accesstoken`, build
and push both images tagged `<sha>`, digests to the run summary) -> `staging` (`ci-rollout.sh`) -> `production`
(`environment: production`; tag the same digests `prod-<sha>`, `ci-rollout.sh`). CI swaps images only (`gcloud run
jobs|services update --image`), so flags, env, secrets and IAM stay with `50`, `60`, `65`. Variables:
deploy/gcloud/README.md "CI deploy".

### 6. Scripts (DEPLOY-PLAN section 0)
- `lib.sh`: sweep/cleanup job names and SAs gone; WORKER_MAX=1, WORKER_CONCURRENCY=2, JOBS_QUEUE_CONCURRENCY=1.
- `20`: SAs api, worker, payments, tasks-invoker, migrate, scheduler. `30`: consumers moved to the worker.
- `40`: jobs queue 1 concurrent; payments `--max-retry-duration=604800s` with 10 s -> 600 s backoff (bounded);
  worker is a jobs-queue enqueuer and may actAs tasks-invoker (the sweep re-enqueues).
- `50`: migrate only. `60`: worker concurrency 2 / max 1, enqueue config, `JOB_STALE_PENDING_SECONDS=3600`, allowed
  emails tasks-invoker + scheduler; api `TRUSTED_PROXY_HOPS=1`; startup probes `/ready` on all three.
- `65`: worker invoker = tasks-invoker + scheduler. `70`: OIDC to the worker run.app URL, audience = that URL,
  `*/5` sweep, `17 *` cleanup, attempt deadline 300 s (allowed [15 s, 30 min]), retries 0, staging paused.
- `80`: deployer run.developer on 3 services + migrate; actAs on 4 runtime identities.
- `90`: payments queue not draining (`queue/depth` > 0 for 1 h). There is no task-age metric (Cloud Tasks metrics:
  api/request_count, queue/depth, queue/task_attempt_count, queue/task_attempt_delays,
  https://docs.cloud.google.com/monitoring/api/metrics_gcp_c); Google Cloud metrics are free
  (https://docs.cloud.google.com/stackdriver/pricing). Manual check in deploy/gcloud/README.md.
- `99-kill-switch.sh stop|resume` (DRY_RUN=1 default): pause/resume both queues and schedules; api and payments
  ingress internal/all (`KEEP_PAYMENTS_PUBLIC=1` keeps payments up). Worker max-instances 0 is not supported (range
  starts at 1, https://docs.cloud.google.com/run/docs/configuring/max-instances); the worker is internal and only
  the paused queues/schedules call it. `--scaling=0` is documented on the manual-scaling page but the gcloud reference
  says "a positive integer"; not used.
- Staging R2 buckets and tokens: owner steps in deploy/gcloud/README.md (Cloudflare, not gcloud).
- New `ci-rollout.sh` (used by the workflow; DRY_RUN=1 locally).

### Pins and sources (looked up 2026-10-17, read-only)
| Item | Pin | Source |
|---|---|---|
| python:3.11.17-slim-trixie (= 3.11-slim) | sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce | registry-1.docker.io manifest HEAD (Docker-Content-Digest, OCI index) and hub.docker.com tags API |
| actions/checkout v7.0.1 | 3d3c42e5aac5ba805825da76410c181273ba90b1 | `gh release list` + `gh api repos/actions/checkout/git/ref/tags/v7.0.1` |
| docker/setup-buildx-action v4.4.1 | f87e5991a6d7451dcb8d9637bfbc97413f497069 | same method |
| docker/build-push-action v7.4.0 | c3c9e263c25d99ce0380d002d59b67737d91b0dc | same method |
| docker/login-action v4.6.0 | dbcb813823bdd20940b903addbd779551569679f | same method |
| google-github-actions/auth v3.0.0 | 7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 | `gh api repos/google-github-actions/auth/tags` |
| google-github-actions/setup-gcloud v3.0.1 | aa5489c8933f4cc7a4f7d45035b3b1440c9c10db | same; `skip_install: true` uses the runner's gcloud (588.0.0 per actions/runner-images Ubuntu2404-Readme) |

## Checks
| Check | Result |
|---|---|
| SQLite `pytest -q` | 181 passed, 13 skipped (baseline 171/13; +10 `test_client_ip.py`) |
| Postgres 16.15 (Homebrew, throwaway cluster on 5433, never 5432) | 194 passed (baseline 184) |
| Failing-first (item 2) | 10 of 10 new tests failed before the fix; direct probe: the spoofed leading entry won |
| OpenAPI regenerated | no diff |
| `bash -n` (macOS /bin/bash 3.2) on deploy/gcloud/*.sh and services/payments-go/scripts/*.sh | clean |
| Dry runs, staging and prod, every script incl. `99 stop`/`resume` and `ci-rollout.sh` | 28 runs, 186 gcloud commands printed, 0 failures; `99 bogus` rejected |
| `set_paused` state logic (lib.sh sourced in a subshell, `gcloud` a stub function, no gcloud on PATH; no deploy script run) | 8 of 8 cases correct |
| `gofmt -l` | clean; `go test` not run (not authorized), the payments-go CI workflow runs it |
| shellcheck, actionlint | NOT RUN: `brew install` fails here (`sandbox-exec: sandbox_apply: Operation not permitted`, also with the agent sandbox off). Fallback: YAML parse of all 4 workflows OK. Owner: registers B2 |
| Docker build | not run locally (no Docker, not allowed); the `image` workflow builds it on the PR |
| Em-dashes, secret patterns in changed files | none |
| Non-test diff (excluding this file) | +711 / -213: at the ~700-line gate (crossed only by the review fixes; deletions are mostly the removed sweep/cleanup jobs). Proposed split if the owner wants smaller PRs: PR A = commit 3faacc6 (app, image, CI, Go) + its review fixes, PR B = scripts 8f427df + kill-switch fixes, PR C = docs. The commits are already cut that way |

## Independent review (subagent, diff 3cd8057..42d01ab), verbatim (nested bullets under "Checked and fine" joined into lines)
> Review of 3cd8057..HEAD (feat/delivery): one finding blocks merge (the kill switch). The rest are SHOULD or NIT. I changed no files. SQLite suite: 181 passed, 13 skipped. tests/test_client_ip.py: 10 passed. I ran the kill switch with DRY_RUN=1 for staging stop and with a bad argument.
>
> ## MUST
>
> **M1. deploy/gcloud/99-kill-switch.sh:37-39 (stop) and 50-53 (resume): stop aborts in staging before it closes ingress.**
> - Cloud Scheduler's REST reference says "A job must be in Job.State.ENABLED to be paused" (fetched today). Resuming has the matching rule that the job must be PAUSED.
> - 70-scheduler.sh leaves the staging jobs PAUSED by default. So `ENV=staging ... stop` pauses both queues, then `gcloud scheduler jobs pause` fails, and `set -e` exits.
> - Result: api and payments are never set to `--ingress=internal`, and the api stays public. That is the spend path the switch exists to close.
> - The same failure happens on a second `stop` in prod (not idempotent), and on `resume` when a job is already ENABLED.
> - Fix:
>   - Read the state first and pause/resume only on ENABLED/PAUSED: `gcloud scheduler jobs describe J --format='value(state)'`.
>   - Do the ingress step first, so the most important cut happens before anything that can fail.
>   - Do the same for queues to be safe (`describe --format='value(state)'`).
> - The same flaw is already in 70-scheduler.sh:43-48 from before this diff: a re-run with PAUSE=1 on a paused job, or PAUSE=0 plus `update` on an enabled job, fails. Log it as a follow-up in the task file.
>
> ## SHOULD
>
> **S1. resume does not restore the exact pre-stop state (99-kill-switch.sh:44-58).**
> - Prod schedules are resumed even if the owner had paused them by hand before `stop`.
> - If KEEP_PAYMENTS_PUBLIC differs between stop and resume, payments stays internal.
> - Ingress is hard-coded to `all`; a later LB setup would need `internal-and-cloud-load-balancing`.
> - Fix: at stop, write the prior states (ingress, queue state, scheduler state) to a label or a local file and replay them on resume. At minimum, document the current behaviour and print the before-state at stop.
>
> **S2. Unpinned code in the deploy chain: .github/workflows/backend-tests.yml:34-35.**
> - `actions/checkout@v7` and `actions/setup-python@v7` (with `cache: pip`) are tag-pinned. deploy.yml now runs this workflow via `workflow_call` on main, ahead of the build.
> - It holds no id-token (the caller grants contents:read, and a called workflow cannot elevate), so it cannot mint GCP tokens. A tag move could still make tests pass falsely and gate a deploy.
> - Fix: SHA-pin both (checkout is already pinned elsewhere: 3d3c42e5...).
>
> **S3. Buildx installs unpinned code (setup-buildx-action in image.yml:21 and deploy.yml:61).**
> - With no `version:` the action pulls the latest buildx binary. The docker-container driver pulls `moby/buildkit:buildx-stable-1` by tag. Both run with the push credentials in the build job.
> - Fix: set `version: vX.Y.Z` and `driver-opts: image=moby/buildkit:vX.Y.Z@sha256:...`.
> - Separately, pip in the builder installs without `--require-hashes`. That is acceptable but worth noting: versions are pinned, hashes are not.
>
> **S4. Concurrency semantics (deploy.yml:18-20).**
> - The workflow-level group `deploy` covers the production job too. A run waiting for prod approval holds the group, and with it every later staging deploy (up to the environment's 30-day wait).
> - GitHub keeps only one *pending* run per group, so a third push silently cancels the queued second push.
> - Fix: use job-level groups (`deploy-staging`, `deploy-prod`), or document that an unapproved prod run blocks staging.
>
> **S5. TRUSTED_PROXY_HOPS=2 is unsafe while the run.app URL is still reachable (config.py:20-23 comment, 60-deploy-services.sh:93-96).**
> - Behind the LB, hops=2 is correct. But a request sent directly to run.app with `XFF: <spoof>` (and one appended entry) gives `entries[-2] = spoof`. The "fewer entries than hops uses leftmost" fallback is also client-controlled in that case.
> - Fix: in the comment and the runbook, state that hops=2 requires `--no-default-url` or ingress `internal-and-cloud-load-balancing`.
> - Also: hops=1 behind an LB is spoof-safe but collapses every anonymous user to the LB IP, so they all share one quota.
> - Default 1 is the right spoof-safe choice for Cloud Run. It is unsafe on any host with no appending proxy, where the only entry is client-supplied. Say so in the comment.
>
> **S6. `--proxy-headers` is effectively a no-op on Cloud Run (Dockerfile:36).**
> - The peer is not 127.0.0.1, so uvicorn trusts neither X-Forwarded-For (intended) nor X-Forwarded-Proto. `request.url.scheme` stays `http`, so any absolute URL or redirect FastAPI builds (redirect_slashes, url_for) will be `http://`.
> - The client-IP logic is not undermined: the app reads the header itself. Note that a `FORWARDED_ALLOW_IPS=*` env var would make `request.client.host` the first entry, which only matters on the hops=0 path.
> - Fix: grep the app for absolute URL building. If any exists, handle the scheme explicitly, or drop the flag to avoid false comfort.
>
> **S7. Verify the IAM edge for `jobs execute --wait` (80-wif.sh:91-92, ci-rollout.sh:20).**
> - run.developer is bound on the job resource. `--wait` polls executions (run.executions.get), and possibly `run.operations.get`.
> - I believe job-level bindings cover child executions, but this is unverified. Check it on the first staging run, or grant `run.viewer` scoped as needed.
> - The rest checks out:
>   - `services update --image` and `services describe` are covered by run.developer on the services.
>   - actAs on the runtime SAs is granted.
>   - `artifacts docker tags add` is covered by artifactregistry.writer on the shared repo, for both deployers.
> - Side note: staging's deployer can also overwrite `prod-*` tags (shared repo, mutable tags). Acceptable, but say so.
>
> ## NIT
>
> - **N1.** DEPLOY-PLAN.md section 1 diagram still says worker "concurrency 1"; Q5 says 2.
> - **N2.** XFF entries with ports or brackets (`1.2.3.4:5678`, `[::1]:443`) are hashed verbatim. That is harmless for Google's port-less format, but normalizing with `ipaddress` would make the hash stable.
> - **N3.** pytest and httpx ship in the runtime image (requirements.txt is shared). Consider a requirements split later.
> - **N4.** The kill switch's queue pause leaves the worker's in-flight request running. Fine, but worth one line in the doc.
>
> ## Checked and fine
>
> - **Action pins:** all 6 `uses:` SHAs in image.yml and deploy.yml match their trailing tags (gh api git/ref/tags; all are lightweight tags pointing at commits): checkout v7.0.1, auth v3.0.0, login-action v4.6.0, setup-buildx-action v4.4.1, build-push-action v7.4.0, setup-gcloud v3.0.1.
> - **Base image:** the digest is `sha256:e88e9763...65f6ce`, which matches Docker Hub's python:3.11.17-slim-trixie index digest (updated 2026-10-09).
> - **No unpinned frontend:** there is no `# syntax=` line.
> - **gcloud:** setup-gcloud uses `skip_install: true`, so no download.
> - **Credentials and permissions:** `persist-credentials: false` everywhere. Workflow default is contents:read, and id-token is granted only on build, staging and production.
> - **WIF:** workflow_ref for every deploy.yml job, including the reusable-call job, is `aindrajaya/imageopz-server/.github/workflows/deploy.yml@refs/heads/main`. The called backend-tests jobs carry the caller's workflow_ref, but they have no id-token. No pull_request or workflow_dispatch trigger. Build (no environment) matches the staging repository_id principalSet. Production matches `repo_env=<id>:production`, and the `has()` guard handles jobs without an environment. The build job's `token_format: access_token` for docker login is covered by workloadIdentityUser. Both CI jobs are dormant behind the guard.
> - **Dockerfile:** multi-stage, with the venv copied from the builder; same base, so the interpreter symlink stays valid. libmagic1t64 (correct on trixie) is in the runtime stage only, and the final stage has no compilers. Runs non-root as uid 10001. PATH includes /opt/venv/bin, WORKDIR /srv contains alembic.ini and alembic/. `IMAGEOPZ_ENV_FILE=""` disables .env loading (config.py:132). The CMD quoting is correct and an invalid SERVICE_ROLE exits 64. The migrate override `--command=alembic --args=upgrade,head` works.
> - **image.yml smoke assertions should all pass:** `id -u` returns non-zero (not root), and no compiler is found. The magic PNG check passes, and `alembic heads` needs no DB. /ready runs SELECT 1, which works on SQLite. The worker returns 401 because INTERNAL_AUTH_MODE is unset, so the request is denied. On the api, /internal/* returns 404.
> - **Build outputs:** with provenance and sbom off, the pushed digest is a plain manifest, as intended.
> - **Client IP:** the rightmost-hops logic is correct across multiple header lines, empty entries, whitespace, and fewer entries than hops. The tests are not vacuous: app.utils.security is not reloaded, so patching `security.settings` hits the object IpFingerprint reads. The hops=0 and hops=2 cases prove the patch takes effect. The `ge=0` validation is tested. The xff_entries log line records counts only (key paths), with no addresses.
> - **Scripts vs DEPLOY-PLAN section 0:** Scheduler calls the worker with OIDC (audience = worker URL; the scheduler SA is allow-listed and has invoker). The jobs queue has concurrency 1 with bounded retries. The payments queue retries for 7 days with backoff capped at 600 s. Worker runs concurrency 2, max-instances 1. Startup probes are on /ready, with no liveness probe. There is a "payments queue not draining" alert (queue depth over 0 for 1 h). `migrate` is the only Cloud Run Job.
> - **Removed jobs:** sweep/cleanup jobs and SAs are gone from 20, 30 and 50; the remaining mentions are in the doc's history note.
> - **Kill switch basics:** DRY_RUN defaults to 1 and bad arguments are rejected (rc=1).
> - **Secrets:** the secret-pattern scan of the diff found nothing.

Disposition:
- M1 fixed: `lib.sh:set_paused` pauses only from ENABLED (scheduler) or RUNNING (queue), resumes only from PAUSED, and
  skips a missing resource. `99 stop` cuts api/payments ingress first and prints the before-state; a failed ingress
  step is reported (exit 1) without skipping the rest. The same pre-existing flaw in `70-scheduler.sh` is fixed with
  the same helper, because 70 is rewritten in this change anyway (stated here, not silent).
- S1 documented in the script: resume restores the deploy defaults, not a snapshot (a hand-paused prod schedule is
  resumed; payments ingress always goes back to all). No state file: extra moving parts for a rare case.
- S2 fixed: `backend-tests.yml` pins checkout v7.0.1 and setup-python v7.0.0
  (5fda3b95a4ea91299a34e894583c3862153e4b97, `gh api repos/actions/setup-python/git/ref/tags/v7.0.0`).
- S3 fixed: buildx `version: v0.38.0` (latest release, 2026-10-07) and BuildKit
  `moby/buildkit:v0.33.1@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea` (what
  `buildx-stable-1` points to today, Docker Hub manifest HEAD). pip without hashes: accepted (exact pins; B1).
- S4 fixed: job-level groups `deploy-build`, `deploy-staging`, `deploy-production`; the one-pending-run behavior is
  documented in the workflow.
- S5 fixed: comments in `app/config.py` and `60`: hops=2 needs the run.app URL closed; hops > 0 is spoofable without an
  appending proxy.
- S6 documented in the Dockerfile (the app builds no absolute URLs; only the trailing-slash redirect would say http).
  The flag stays, as the contract asks. The reviewer's "the peer is not 127.0.0.1" is undocumented; worded as expected.
- S7: open check for the first CI run (DEPLOY-PLAN section 4); prod-tag mutability noted there.
- N1 fixed (diagram). N2 not done (Google's format has no ports). N3 = registers B1. N4: the stop message says
  in-flight requests finish.

## Follow-ups (not fixed here)
- B1 requirements split (runtime vs test). B2 shellcheck/actionlint by the owner.
- X1 / checklist item 15: confirm `xff_entries` in staging; set `TRUSTED_PROXY_HOPS=2` only behind an LB with the
  run.app URL closed.
- S7: whether `run.developer` on the job is enough for `jobs execute --wait`; verify on the first staging run (T-008c).
- Still open: A1 (`60` sets `EXPOSE_API_DOCS` but the app does not implement it), S4 (OIDC cert cache), SECRET_KEY
  scope, checklist item 19 (create_task timeout).

## Owner commands
```
brew install shellcheck actionlint && shellcheck deploy/gcloud/*.sh services/payments-go/scripts/*.sh && actionlint
git push -u origin feat/delivery
gh pr create --base main --head feat/delivery --title "T-008b2 delivery: /ready, client-IP trust, Python image, CI image check, dormant deploy workflow, scripts sync"
gh pr checks <n> --watch && gh pr merge <n> --squash --delete-branch
```
Expected checks: `pytest (sqlite)`, `pytest (postgres)`, `image`, and the payments-go jobs (its path filter matches
this change). After the merge, add `image` to ruleset 25125469 once that exact name shows in `gh pr checks`, and record
the squash SHA and PR number in STATE.md.
