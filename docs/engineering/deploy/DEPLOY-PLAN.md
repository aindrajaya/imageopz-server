# DEPLOY-PLAN - ImageOpz on Cloud Run (staging + prod)

Status: owner decisions Q1-Q11 recorded 2026-10-17 (section 0); updated by T-008b1. Nothing in this plan has been
created. Scripts: `deploy/gcloud/` (DRY_RUN=1 by default); they still reflect T-008a (Cloud Run Jobs for sweep and
cleanup, 24 h payments retry) and are brought in line with section 0 by T-008b2. App changes: T-008b1 (route roles,
maintenance endpoints, H1, C1, header-only sessions); Dockerfile, CI, A1, S4: T-008b2.

## 0. Owner decisions (2026-10-17)
| Q | Decision | Where it lands |
|---|---|---|
| Q1 | Scheduled sweep and cleanup: Cloud Scheduler -> worker `POST /internal/maintenance/{sweep,cleanup}` with OIDC. Migrations stay a Cloud Run Job; the CLIs stay for manual runs. | sections 2, 4, 10; ADR-005 amendment; code T-008b1 |
| Q2 | Option B: run.app URLs, anonymous session by `X-Session-Id` header, cookies off in the UI (`VITE_IMAGEOPZ_SEND_COOKIES=false`). | section 7; header-only flow tested (T-008b1); UI follow-up T-008b3 |
| Q3 | Staging UI origin `https://staging.imageopz.aindrajaya.my.id`; prod `https://imageopz.aindrajaya.my.id`. | section 5 (`CORS_ALLOW_ORIGINS`), section 7 |
| Q4 | Separate staging R2 buckets `imageopz-staging` / `imageopz-uploads-staging` with their own scoped tokens. | section 5 |
| Q5 | Provisional sizing as proposed, except the worker: max-instances 1 in both environments. Revisit after the item-10 measurement. | sections 2, 5 (G1) |
| Q6 | U1 presign abuse risk accepted at go-live (owner, 2026-10-17) with cheap bounds only (below). | registers.md Risks (U1) |
| Q7 | Staging Supabase = a second free project in the same account. | section 1 |
| Q8 | Budget alerts plus a manual kill switch (below; script in T-008b2). No spend-cap budget. | section 9 |
| Q9 | Payments queue retries for 7 days, with an alert on 5xx. | section 2 |
| Q10 | Pending: owner checks for an org policy blocking `allUsers`. | open |
| Q11 | Deferred (J1 refund policy, payments follow-up). | registers.md J1 |

Q6 cheap bounds (no limiter in code): 3 free credits per session id and the tier's per-request file count and size
limits; api max-instances 3; worker max-instances 1 and the jobs queue's dispatch limits (2/s, 1 concurrent) cap
processing cost whatever the number of uploads; R2 lifecycle deletes `uploads/` after 1 day (U3); budget alerts at
50/90/100 % of 10 USD and the kill switch. Residual risk: an attacker rotating session ids gets free conversions and
fills the jobs queue backlog (other users wait); storage and compute spend stay bounded.

Kill switch (Q8; `deploy/gcloud/95-kill-switch.sh`, T-008b2): one owner command per environment that stops spend
without deleting anything: pause both Cloud Tasks queues (tasks are kept, up to 31 days), pause the Scheduler jobs,
and set the api and payments services to `--ingress=internal` (public requests get 404 from Google's front end;
LemonSqueezy webhooks fail and are retried by LemonSqueezy for a limited time, unverified, so prefer pausing the
payments queue alone while checkout is live). `--resume` reverses each step. Trigger: a budget alert email or the
J5 / queue-depth alerts.
Inputs: ADR-001..005, registers.md (T-008 checklist, G1, S4, A1, U1-U3, U15, J1, J2, J5), T-005/T-006b/T-007 hand-offs,
`app/config.py`, `services/payments-go/internal/config`, imageopz-ui STATE (R2 CORS for staging) at f6f19ce.

GCP project `imageopz-prod-4821`, region `asia-northeast1` (Tokyo, Cloud Run Tier 1 [R1]), co-located with Supabase
ap-northeast-1 (ADR-002 amendment). All doc URLs were read on 2026-10-16; the reference list is at the end ([Rn]).

## 1. Architecture

```
 Browser (Vercel UI) --HTTPS--> imageopz-api-<env>       (public routes only; ingress all; allUsers invoker)
        |                         |  CreateTask (OIDC as iz-tasks-invoker)
        |  presigned PUT          v
        +-----> R2 uploads    Cloud Tasks imageopz-jobs-<env> ----+
                                                                  |  run.app URL, OIDC, internal ingress
 LemonSqueezy --HTTPS--> imageopz-payments-<env> (Go ingress)     v
                           |  CreateTask              imageopz-worker-<env>  (/internal/* only, concurrency 1)
                           +--> Cloud Tasks imageopz-payments-<env> --^       |-> R2 outputs (upload), R2 uploads (read/delete)
 Cloud Scheduler --OIDC as iz-scheduler--> worker run.app URL /internal/maintenance/{sweep,cleanup} (Q1)
 CI (GitHub Actions, WIF) -> Artifact Registry imageopz -> Cloud Run Job imageopz-migrate-<env> (alembic) -> services
 All Python processes -> Supabase pooler: transaction mode :6543 (api, worker), session mode :5432 (migrate)
```

### One project, suffixed resources (recorded trade-off)
Both environments live in `imageopz-prod-4821`; every resource carries `-staging` or `-prod`, and every runtime
identity, secret, queue and job is per environment. Artifact Registry is shared on purpose (one digest promoted).
- For: one set of enabled APIs, one budget, one WIF pool, no second billing link; the Cloud Run free tier is per billing
  account anyway [R2], so a second project would not add free capacity. Simpler for a solo maintainer.
- Against: no hard IAM boundary. Project Owner/Editor reaches both environments; quotas (Cloud Run instances, Tasks)
  and the Logging free allotment (50 GiB per project [R2]) are shared; a staging mistake with project-level scope
  could touch prod.
- Mitigation: no project-level role is granted to any workload or CI identity. Every binding is resource-scoped
  (secret, queue, service, job, service account), and the CI deployers are per environment (section 4).
- Revisit: first paying customer, a second maintainer, or any need for project-level roles in CI. Then split into
  `imageopz-staging-*` and keep this project for prod.

Supabase: one free project per environment (Free plan: "Limit of 2 active projects" [R20]). Staging pausing after
a week of inactivity [R20] is acceptable; restore it before a staging session.

## 2. Resource inventory (exact names; `<env>` = staging | prod)

### Artifact Registry
| Resource | Name | Notes |
|---|---|---|
| Docker repo | `asia-northeast1-docker.pkg.dev/imageopz-prod-4821/imageopz` | images `api` (Python: api, worker, 3 jobs) and `payments-ingress` (Go) |
| Cleanup policy | `deploy/gcloud/ar-cleanup-policy.json` | delete > 30 d; keep 5 most recent; keep tags `prod-*` (keep wins over delete [R11]); starts in dry-run |

### Cloud Run services
| | `imageopz-api-<env>` | `imageopz-worker-<env>` | `imageopz-payments-<env>` |
|---|---|---|---|
| Image | api@digest | api@digest | payments-ingress@digest |
| Routes | `SERVICE_ROLE=api`: public `/api/v1/*`, `/`, `/health`, `/readyz`, `/version` (no `/internal`) | `SERVICE_ROLE=worker`: `/internal/jobs/process`, `/internal/payments/events`, `/internal/maintenance/{sweep,cleanup}`, `/health`, `/readyz`, `/version` | `/webhooks/lemonsqueezy`, `/healthz`, `/readyz` |
| CPU / memory | 1 / 512Mi | 2 / 4Gi (measure in staging, Q5) | 1 / 512Mi |
| Concurrency | 20 | 2 (one image job + one payment or maintenance call; see Cloud Tasks queues) | 8 (= Go pool MaxConns) |
| Min / max instances staging | 0 / 2 | 0 / 1 (Q5) | 0 / 1 |
| Min / max instances prod | 0 / 3 | 0 / 1 (Q5) | 0 / 2 |
| Request timeout | 60 s | 1800 s (>= Tasks dispatch deadline, ADR-005) | 30 s |
| Ingress | all | internal | all |
| Auth | allUsers invoker | IAM: only `iz-tasks-invoker-<env>` has run.invoker; app also verifies OIDC | allUsers invoker (HMAC in app) |
| Service account | `iz-api-<env>` | `iz-worker-<env>` | `iz-payments-<env>` |
| Other | `--cpu-boost` | `--cpu-boost`, gen2 | - |

Limits relied on: timeout up to 3600 s, default 300 s [R3]; concurrency up to 1,000, gcloud default 80 x vCPU [R4];
1 vCPU up to 4 GiB, 2 vCPU up to 8 GiB, gen2 at least 512 MiB [R5]; request-based billing is the default [R6]; default
max instances 100 per revision, min 0 [R7]. Images are referenced by digest [R8].

### Cloud Run Jobs
| Job | Command | SA | Timeout / retries | Schedule |
|---|---|---|---|---|
| `imageopz-migrate-<env>` | `alembic upgrade head` (DATABASE_URL = session pooler :5432) | `iz-migrate-<env>` | 600 s / 0 | by CI before each rollout |

The T-008a sweep and cleanup jobs (`imageopz-sweep-uploads-<env>`, `imageopz-cleanup-expired-<env>`, SAs `iz-sweep-<env>`,
`iz-cleanup-<env>`) are dropped by Q1; T-008b2 removes them from `20`, `30`, `50`, `65` and `70`. 1 vCPU / 512Mi, 1 task. Task timeout default 10 min, up to 168 h; max retries 0-10, default 3 [R9].

### Cloud Scheduler (Q1, ADR-005 amendment)
| Scheduler job | Target (HTTP POST, worker run.app URL) | Schedule (Etc/UTC) |
|---|---|---|
| `imageopz-sweep-uploads-<env>` | `/internal/maintenance/sweep` | `*/5 * * * *` (U2) |
| `imageopz-cleanup-expired-<env>` | `/internal/maintenance/cleanup` | `17 * * * *` (U2) |

In asia-northeast1 (supported [R13]), OIDC token for `iz-scheduler-<env>` with audience = the worker run.app URL (the
same pattern as Cloud Tasks; `ingress=internal` admits Scheduler on the run.app URL in the same project [R15]). The
worker's `INTERNAL_AUTH_ALLOWED_EMAILS` lists `iz-tasks-invoker-<env>` and `iz-scheduler-<env>`. Attempt deadline
300 s; scheduler retries 0 (the next tick is the retry). Staging schedules are created PAUSED and resumed only while
testing. Responses: 200 with counts; 503 `{"status": "retry"}` on a DB error, a failed re-enqueue or a failed R2
delete (logged at ERROR, so the log alert fires); 401 auth. Both endpoints are idempotent. A manual CLI run
overlapping a tick is safe: the conditional UPDATE on `enqueue_attempts` lets only one sweep claim each stale job,
and any duplicate delivery is a no-op under the worker lease (J3).
Cost: request-based billing on the worker instead of a 1-minute minimum per Cloud Run Job execution [R2], so the U2
cadence fits the free tier (section 10). The sweep's DB queries every 5 min also count as Supabase activity.
Busy worker: with max-instances 1 and concurrency 2, a tick that finds both slots taken (one image job plus one
payment) is refused by Cloud Run and logged as a failed attempt; the next tick retries.

Backlog vs the sweeper (review finding 2): the jobs queue dispatches 1 (staging) or 2 (prod) jobs at a time. Jobs
waiting in the Cloud Tasks backlog stay PENDING with their original `enqueued_at`. With the default
`JOB_STALE_PENDING_SECONDS=600`, the sweeper would add `-r1..-r3` duplicates to a legitimate backlog and then FAIL the
tail of a 30-image Pro batch (~1 h at concurrency 1). The worker (which runs the sweep, Q1) therefore
runs with `JOB_STALE_PENDING_SECONDS=3600`, which tolerates a backlog of about 1 h. A genuinely lost enqueue (U8) is recovered
after ~1-1.25 h instead of ~10-25 min. 3600 s also clears the 600 s max backoff, so a transient-failure retry is
never mistaken for a lost task. The queue-depth alert (depth > 10 for 15 min) fires well before that horizon. A
longer backlog needs more worker instances first (G1 table).

### Cloud Tasks queues
| | `imageopz-jobs-<env>` | `imageopz-payments-<env>` |
|---|---|---|
| Target | worker run.app URL + `/internal/jobs/process` | worker run.app URL + `/internal/payments/events` |
| OIDC SA / audience | `iz-tasks-invoker-<env>` / worker run.app URL | same |
| Enqueuers | `iz-api-<env>`, `iz-worker-<env>` (sweep re-enqueue) | `iz-payments-<env>` |
| max-attempts | 15 | 50 |
| max-retry-duration | 7200s | 604800s (7 days, Q9) |
| min / max backoff, doublings | 30s / 600s, 5 | 10s / 600s, 6 |
| max-dispatches-per-second | 2 | 5 |
| max-concurrent-dispatches | 1 (worker max 1, concurrency 2: one slot stays free) | 1 |
| log-sampling-ratio | 1.0 | 1.0 |

J3 requirement (ADR-005): a delivery during a live lease answers 503, so retries must outlast `JOB_LEASE_SECONDS=1800`.
Cloud Tasks "stops retrying only when maxAttempts and maxRetryDuration are both satisfied" [R10]. The retry gaps
are 30, 60, 120, 240 and 480 s, then 600 s each. The 7th retry comes ~2,130 s after the first failure, past the lease.
Retries end once both 15 attempts and 7,200 s have passed: the 16th attempt lands at ~6,930 s, the 17th at ~7,530 s,
then retries stop. That is bounded. It outlasts the sweeper's FAIL of PROCESSING jobs at 2x the lease (3,600 s); a
delivery that reaches an already FAILED job is a 2xx no-op (ADR-005), so the chain ends there. This holds even if
the first attempt runs to the 1,800 s dispatch deadline, because the retry duration counts from the first attempt.
Durations end in `s`; `-1` attempts would mean unlimited (not used) [R10].
Payments: the ingress has already answered 2xx to LemonSqueezy, so this queue is the only retry path for an
activation. Q9: 7 days of retries (at the 600 s max backoff, ~1,000 attempts; the 50-attempt floor is reached
first, so the duration governs) and an alert on any 5xx (section 9). Exhaustion after 7 days still needs a manual
replay, now an unlikely edge case.
Limits: 1 MiB task, 500 dispatches/s per queue, 31-day retention, task names reserved up to 24 h [R14].
Q5 sets the worker to max-instances 1. The jobs queue dispatches one image job at a time and the worker runs at
concurrency 2, so the second slot stays free for a payment event or a maintenance call, which never waits behind a
30-min image job. Memory: one image job plus a light request fits 4 GiB (confirm with item 10).

## 3. Route exposure
- `ingress=internal` admits Cloud Tasks and Cloud Scheduler "if they're in the same project or VPC Service Controls
  perimeter" and "using the default run.app URL and not a custom URL or custom domain" [R15]. So the worker keeps its
  run.app URL, and both queues target it (deterministic URL `https://SERVICE-PROJECT_NUMBER.REGION.run.app`, known
  before the first deploy [R16]). The same URL is the OIDC audience; Cloud Run always accepts it as `aud` [R17].
- The worker is `--no-allow-unauthenticated`: Cloud Run's IAM check admits only principals holding
  `roles/run.invoker` on that service [R18]. The app then verifies the token again (ADR-004, `INTERNAL_AUTH_MODE=oidc`,
  audience = worker URL, allowed email = `iz-tasks-invoker-<env>`), giving two independent checks.
- `SERVICE_ROLE` = `api` | `worker` | `all` (implemented T-008b1; default `all` for local dev and tests; any other value
  fails settings validation, so the process does not start). `api` does not mount the internal router (`/internal/*`
  is 404). `worker` mounts only the health routes (`/health`, `/readyz`, `/version`) and the internal router: no
  public routes, no CORS, no `/docs`, `/redoc` or `/openapi.json`. The api still serves them until A1 (T-008b2);
  the internal routes are excluded from the schema. Defense in depth: the api service also runs without
  `INTERNAL_AUTH_MODE`, so even a mounted `/internal/*` would refuse every call with 401.
- Health (H1, T-008b1): `/health` is liveness only (generic body, no DB check, never error text); `/readyz` checks the
  database and answers 503 `{"status": "unavailable"}` on failure. Unverified: whether Google's front end serves
  paths ending in `z` from outside on run.app; check `/readyz` from a browser in staging. Probes reach the container
  directly either way.
- The api's public fallback `POST /api/v1/webhooks/lemonsqueezy` stays mounted (ADR-001 rollback path); it needs
  `LEMONSQUEEZY_SECRET`.

## 4. IAM matrix (per environment; no service-account keys anywhere)
| Identity | Grants (all resource-scoped) |
|---|---|
| `iz-api-<env>` | secretAccessor on database-url, secret-key, r2-uploads-access-key-id, r2-uploads-secret-access-key, lemonsqueezy-signing-secret, lemonsqueezy-api-key; `roles/cloudtasks.enqueuer` on the jobs queue; `roles/iam.serviceAccountUser` on `iz-tasks-invoker-<env>` (OIDC actAs [R19]) |
| `iz-worker-<env>` | secretAccessor on database-url, secret-key, r2-outputs-*, r2-uploads-*; enqueuer on the jobs queue and serviceAccountUser on `iz-tasks-invoker-<env>` (the sweep re-enqueues stale jobs, Q1) |
| `iz-payments-<env>` | secretAccessor on database-url, lemonsqueezy-signing-secret; enqueuer on the payments queue; serviceAccountUser on `iz-tasks-invoker-<env>` |
| `iz-tasks-invoker-<env>` | `roles/run.invoker` on `imageopz-worker-<env>` only. Holds nothing else. |
| `iz-migrate-<env>` | secretAccessor on database-url-session, secret-key |
| `iz-scheduler-<env>` | `roles/run.invoker` on `imageopz-worker-<env>` only (Q1). Holds nothing else. |
| Cloud Tasks service agent | serviceAccountUser on `iz-tasks-invoker-<env>` (doc conflict, see note) |
| `iz-deployer-<env>` (CI) | `roles/iam.workloadIdentityUser` for the WIF principalSet; `roles/run.developer` on the env's 3 services and 3 jobs; `roles/artifactregistry.writer` on the repo; serviceAccountUser on the env's 6 runtime identities (deploy actAs [R22]) |
| Owner (human) | project Owner: runs the scripts, adds secret versions, sets public invoker, runs downgrades |

Notes
- Logging: Cloud Run collects stdout/stderr and request logs automatically [R23]; no logging role is granted.
- Default compute SA: Cloud Run would otherwise use it, and it "might automatically be granted the Editor role" [R24].
  Every service and job sets `--service-account`.
- Cloud Tasks token minting: the service agent's default role carries `getOpenIdToken`, but the HTTP-target guide
  also asks for Service Account User on the OIDC account [R19]. The grant is cheap and scoped to one account, so 40 adds it.
- CI cannot change IAM: `run.developer` is not `run.admin` [R22]. The allUsers invoker on api/payments is set once by
  the owner (`65-service-iam.sh`), and CI deploys do not pass `--allow-unauthenticated`.
- WIF (`80-wif.sh`): provider condition
  `assertion.repository_id=='<id>' && assertion.repository_owner_id=='<id>' && assertion.ref=='refs/heads/main'`.
  It uses numeric ids because names can be reused [R25], and Google requires a condition for GitHub [R25].
  The condition also pins `assertion.workflow_ref` to `.github/workflows/deploy.yml@refs/heads/main` (T-008b creates
  that file). Without the pin, a later `pull_request_target` or `workflow_run` workflow, which also runs with
  ref == main, could mint deployer tokens. Impersonation is used, not direct federation, because "Cloud Run doesn't
  support Workload Identity Federation direct resource access" [R26]. Staging deployer: the deploy workflow. Prod
  deployer: only that workflow's job in the GitHub Environment `production`, matched by
  `attribute.repo_env = <repository_id>:<environment>`, so no other repo or provider in the pool can match. The
  workflow puts the owner approval gate on that job.
  Validate the `has(assertion.environment)` mapping on the first CI run with both an environment job and a plain job.
- Risk to check on the first CI run: whether `run.developer` granted per service/job is enough for
  `gcloud run deploy` and `gcloud run jobs execute --wait` (operation polling, `run.executions.get`, revision reads). Fallback: project-level `roles/run.developer` on the
  deployers only (both envs; recorded deviation).
- Overrides (accepted risk): `gcloud run jobs execute --args` needs `run.jobs.runWithOverrides` [R21], and
  `roles/run.developer` includes it, along with job/service delete. A compromised deploy workflow on main could
  therefore run `alembic downgrade` on prod. Mitigations: main is protected (PR required), the WIF condition pins the
  deploy workflow, and prod needs the Environment approval. By policy only the owner runs downgrades.
  Hardening (T-008b, optional): replace run.developer on the jobs with a custom role
  (`run.jobs.get/update/run`, `run.executions.get/list`, `run.operations.get`).
- One OIDC identity serves both internal routes (review finding 9). An identity allowed to actAs it (api, worker) could
  craft a jobs-queue task aimed at `/internal/payments/events`. That gives api nothing new, since it holds the
  signing secret; the worker already serves that route. Q1 adds `iz-scheduler-<env>` to the same allowed-email list,
  so the scheduler identity could also call the job and payment routes; only Cloud Scheduler can mint its tokens
  (no one is granted actAs on it besides the owner). Follow-up: one invoker identity per route plus a per-route
  allowed-email check in the app.

## 5. Secrets and configuration

### Secret Manager (`imageopz-<name>-<env>`, user-managed replication in asia-northeast1, no versions created by scripts)
| Secret | Env var | Consumers |
|---|---|---|
| database-url | `DATABASE_URL` (transaction pooler :6543, table-owner role) | api, worker, payments |
| database-url-session | `DATABASE_URL` (session pooler :5432, table-owner role) | migrate |
| secret-key | `SECRET_KEY` | api; also worker and migrate until the SECRET_KEY scope change (T-008b2, see below) |
| r2-outputs-access-key-id / r2-outputs-secret-access-key | `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | worker |
| r2-uploads-access-key-id / r2-uploads-secret-access-key | `R2_UPLOADS_ACCESS_KEY_ID` / `R2_UPLOADS_SECRET_ACCESS_KEY` | api, worker |
| lemonsqueezy-signing-secret | `LEMONSQUEEZY_SECRET` (api), `LEMONSQUEEZY_SIGNING_SECRET` (Go) | api (fallback route), payments |
| lemonsqueezy-api-key | `LEMONSQUEEZY_API_KEY` | api |

URL format: both database secrets hold a bare `postgresql://postgres.<ref>:<password>@<pooler-host>:<port>/postgres`
(add `?sslmode=require`). Never use `postgresql+psycopg://`: the Go ingress shares `database-url`, and pgx rejects that
scheme, so the ingress would crash-loop. Python maps the bare form to psycopg3 and disables prepared statements
(`app/config.py:sqlalchemy_database_url`, `app/models.py`).
Not provisioned: `RESEND_API_KEY` (`app/services/email_service.py` has no caller), `INTERNAL_AUTH_SHARED_SECRET` and
`INTERNAL_SHARED_SECRET` (local/direct mode only), `SUPABASE_ANON_KEY` (publishable, configured as plain config).
SECRET_KEY scope: `Settings._validate_production_settings` rejects a dev SECRET_KEY whenever `ENVIRONMENT=production`.
Every Python process in prod (worker, CLIs, Alembic's `env.py`) therefore needs it. T-008b should apply the check only
where the key is used (api), then remove the extra accessor bindings.
Values: the owner adds each value with `gcloud secrets versions add <secret> --data-file=-` (stdin [R27]). Nothing
passes through arguments, files in the repo, or logs. Services read `:latest` at instance start. Rotation: add a
version, deploy a new revision, then disable the old version.
Secret Manager decisions: per-secret bindings ("if a principal needs access to a single secret, don't give that
principal access to other secrets" [R27]); one location, because user-managed replication is billed per location [R28].

### Non-secret configuration per environment
| Var | staging | prod | Where |
|---|---|---|---|
| `ENVIRONMENT` | staging | production | all Python |
| `DEBUG` | false | false | all Python |
| `SERVICE_ROLE` (T-008b1) | api / worker | api / worker | api, worker |
| `EXPOSE_API_DOCS` (T-008b2, A1) | true | false | api |
| `CORS_ALLOW_ORIGINS` | `https://staging.imageopz.aindrajaya.my.id` (+ `http://localhost:3000` if wanted) (Q3) | `https://imageopz.aindrajaya.my.id` | api |
| `CORS_ALLOW_CREDENTIALS` | true until T-008b3, then false (section 7, Q2) | same | api |
| `DB_POOL_SIZE` | 2 (api, worker) | same | Python |
| `JOB_ENQUEUE_MODE` | cloudtasks | cloudtasks | api, worker (the sweep re-enqueues, Q1) |
| `JOB_TASKS_QUEUE` | `projects/imageopz-prod-4821/locations/asia-northeast1/queues/imageopz-jobs-<env>` | | api, worker |
| `JOB_TASKS_TARGET_URL`, `JOB_TASKS_AUDIENCE` | worker run.app URL | | api, worker |
| `JOB_TASKS_SA_EMAIL` | `iz-tasks-invoker-<env>@imageopz-prod-4821.iam.gserviceaccount.com` | | api, worker |
| `JOB_LEASE_SECONDS` / `JOB_STALE_PENDING_SECONDS` | 1800 / 3600 (see section 2) | same | worker |
| `INTERNAL_AUTH_MODE` / `_AUDIENCE` / `_ALLOWED_EMAILS` | oidc / worker URL / `iz-tasks-invoker-<env>` and `iz-scheduler-<env>` emails | same | worker only |
| `R2_ACCOUNT_ID` | owner | owner | api, worker |
| `R2_BUCKET_NAME`, `R2_CUSTOM_DOMAIN` | `imageopz-staging` (Q4) | `imageopz` | worker |
| `R2_UPLOADS_BUCKET` | `imageopz-uploads-staging` (Q4) | `imageopz-uploads` | api, worker |
| `SUPABASE_JWKS_URL`, `_JWT_ISSUER`, `_AUTH_URL`, `_ANON_KEY`, `_JWT_AUDIENCE=authenticated` | staging Supabase project | prod project | api |
| `LEMONSQUEEZY_STORE_ID`, `_VARIANT_ID`, `_REDIRECT_URL` | test-mode store | live store | api |
| `LEMONSQUEEZY_TEST_MODE` | true | false | api |
| Go: `ENQUEUE_MODE=cloudtasks`, `TASKS_QUEUE` (payments queue path), `TASKS_TARGET_URL` = `TASKS_AUDIENCE` = worker URL, `TASKS_SA_EMAIL` = invoker SA | | | payments |
| Image limits, quotas, expiry, session cookie | defaults from `app/config.py` | same | - |

Env vars are applied with `--env-vars-file`, which replaces all variables on each deploy (declarative), and commas
in CORS lists need no escaping [R29]. `--set-secrets` replaces all secret refs the same way [R8].

### DB pool sizing vs the Supabase pooler (G1)
Supabase Nano (Free): 60 max database connections, 200 pooler max clients [R30]. The shared pooler is IPv4 on
every plan. Port 6543 is transaction mode, without prepared statements, which the app already disables (ADR-003). Port
5432 on the pooler host is session mode [R31].

| Client (prod) | Pool per instance | Max instances | Max client connections |
|---|---|---|---|
| api | 2 (`DB_POOL_SIZE`, max_overflow 0) | 3 | 6 |
| worker | 2 | 1 (Q5) | 2 |
| payments (Go pgxpool MaxConns 8) | 8 | 2 | 16 |
| migrate | NullPool 1 | 1 | 1 |
| Total | | | 25 |

That is 25 of 200 pooler clients (staging, with its own Supabase project, is 15). Transaction mode holds a backend
connection only for the length of a transaction, so active backends stay far below 60. The Supavisor FAQ's 40 %/80 %
guidance [R32] is met even if every client is busy at once. The Go ingress has concurrency 8, equal to its pool, so
requests never queue for a connection. The api runs concurrency 20 on a pool of 2: DB work queues inside the instance
(30 s pool timeout) rather than at Supabase. Raising max-instances needs this table updated first.

## 6. Image strategy (CI, T-008b)
1. On push to `main` (after `pytest (sqlite)` / `pytest (postgres)` pass): build `api` (new root Dockerfile, T-008b:
   Python 3.11, libmagic, uvicorn on `$PORT`, includes `alembic/` and `alembic.ini`) and `payments-ingress`
   (`services/payments-go/Dockerfile`). Tag both `<git-sha>`, push to Artifact Registry, and record the digests.
2. Staging (deployer `iz-deployer-staging`): `gcloud run jobs deploy imageopz-migrate-staging --image=<api digest>`,
   then `gcloud run jobs execute ... --wait` [R9]. Then deploy worker, api and payments by digest (same flags as
   `60-deploy-services.sh`, but without `--no-allow-unauthenticated`/`--allow-unauthenticated`: IAM is set once by
   the owner, and those flags may try an IAM policy update that run.developer cannot do). Smoke test: `/health`, `/readyz` and `/version` on the api,
   `/healthz` and `/readyz` on payments.
3. Prod (GitHub Environment `production`, owner approval, deployer `iz-deployer-prod`): the same digests are tagged
   `prod-<sha>`, which the cleanup policy keeps. The steps are migrate, wait, then the services. Nothing is rebuilt.
4. Migrations run before the new revision serves traffic. The previous revision keeps serving during the migration,
   so every migration must be compatible with the previous release (expand/contract, the pattern T-006b2 and T-007
   already follow). A failed migrate stops the pipeline (`--max-retries=0`).
5. Rollback: section 12. Downgrade policy: application rollback never downgrades the schema by default. A downgrade
   is an owner action after a fresh dump (Supabase Free has no backups; it recommends `supabase db dump` [R33]).
GitHub Actions in the new workflow are pinned by commit SHA, and `permissions: id-token: write` is set only on deploy
jobs.

## 7. Networking and domains
- API URL options:
  (a) run.app URLs: GA, free, deterministic, and stable across redeploys. **Recommended for go-live.**
  (b) Cloud Run domain mapping: asia-northeast1 is supported, but the feature is Preview, "not recommended for
      production services", cannot disable TLS 1.0/1.1, and certificates can take up to 24 h [R34].
  (c) Global external Application Load Balancer with a serverless NEG: Google's recommended custom-domain path [R34],
      but it adds a load balancer to run and pay for (price not verified here). Out of budget for now.
  (d) Firebase Hosting rewrites: low cost, not covered by the Google Cloud Terms of Service [R34].
  Cookie impact: the session cookie is `SameSite=Lax` (`app/routes/session.py`). From a UI on a different site
  (run.app vs the UI's domain) the browser does not send it on fetch calls, so the UI must rely on `X-Session-Id`
  (`VITE_IMAGEOPZ_SEND_COOKIES=false`, imageopz-ui `.env.example`). With (b) or (c) on a same-site host (e.g.
  `api.<ui-domain>`) the cookie also works.
  Decision Q2 = (a) with header sessions. The server still sets the cookie (now `Secure` outside development, C1); the
  browser simply never sends it cross-site. `tests/test_header_only_session.py` proves the anonymous flow (session/me,
  presign, complete, status, reconcile, restore, logout) with the header and no cookie; no route needed a change.
  The UI must create and persist the id itself (it does: `localStorage` `imageopz_session_id`, imageopz-ui
  `src/App.tsx`), because a request without the header gets a fresh id each time. UI follow-up T-008b3:
  `logoutSession` (`lib/imageopzApi.ts`) always sends `credentials: "include"`, ignoring the flag. Until it is fixed,
  keep `CORS_ALLOW_CREDENTIALS=true`, or the browser blocks the logout response; afterwards set it to false.
- The worker must stay on its run.app URL (internal ingress + Cloud Tasks [R15]).
- `CORS_ALLOW_ORIGINS`: prod = the prod UI origin(s) only (`https://imageopz.aindrajaya.my.id` per the uploads contract;
  confirm, Q3); staging = the stable staging UI origin. Never Vercel preview URLs (one per commit) and never `*`
  (rejected in production by the config validator).
- R2 uploads bucket CORS (dashboard JSON or `wrangler r2 bucket cors set` [R35]): methods `PUT, GET`, header
  `content-type`, max-age 3600. The prod bucket allows the prod origin only (drop `localhost:3000` once staging has
  its own bucket). The staging bucket allows the staging origin and `http://localhost:3000`. Propagation can take up
  to 30 s [R35]. U3: lifecycle rule `uploads/` expire after 1 day on both upload buckets
  (`wrangler r2 bucket lifecycle add <bucket> <name> uploads/ --expire-days 1` [R36]).
- UI (Vercel): Production environment uses `VITE_IMAGEOPZ_API_BASE_URL` = prod api URL. A fixed staging domain is
  assigned to a long-lived branch (e.g. `staging`) with the staging api URL. Preview deployments of other branches
  either point at staging and are not in CORS (expected to fail uploads), or are not used. Also set
  `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` per environment and `VITE_SUPABASE_REDIRECT_URL` (the Supabase project's
  allowed redirect URLs must include it). Vercel mechanics are outside this repo; owner verifies (Q3).
- LemonSqueezy: the webhook URL switches from the old API route to
  `https://imageopz-payments-prod-<PROJECT_NUMBER>.asia-northeast1.run.app/webhooks/lemonsqueezy`, with the signing
  secret identical to `imageopz-lemonsqueezy-signing-secret-prod`. Staging uses the test-mode store's webhook and
  points at `imageopz-payments-staging`.

## 8. /docs, /redoc, /openapi.json (A1)
Recommendation: off in prod, on in staging. Add an `EXPOSE_API_DOCS` setting, default true for development and false
when `ENVIRONMENT=production`; the app factory passes `docs_url/redoc_url/openapi_url=None` when it is false (T-008b).
Reasons: the public contract is already published in the repo (`docs/openapi.json`, regenerated per change);
interactive docs on the live API add attack-surface discovery and help scripted free-credit abuse (U1) without
helping users. `/internal/*` is already excluded from the schema (`tests/test_openapi_schema.py`). The root endpoint
`/` advertises `"docs": "/docs"`; T-008b should drop that field when docs are off.

## 9. Observability (free-tier friendly; `90-observability.sh`)
- Alerting policies are not charged before 2027-09-01; after that $0.35 per metric reference per month [R37].
  Log-match policies return no points. Email channels: no line item found (unverified).
- Log-match alerts, at most one notification per hour:
  - payment event failed at the API (worker `/internal/payments/events` 5xx)
  - payments ingress 5xx
  - ERROR logs on the api and worker (covers J5's catch-all 503 for bugs, which logs the exception)
  - ERROR logs from any `imageopz-*-<env>` job (`resource.type=cloud_run_job` [R23]): the CLIs log an error before a
    non-zero exit (the T-007 "alert on non-zero exit" item)
  - The app logs plain text (`logging.basicConfig`), and the severity Cloud Run assigns to those lines is unverified.
    The filters therefore match `severity>=ERROR OR textPayload=~" - ERROR - "`. Each policy must be seen firing in
    staging (item 17). Structured JSON logs with a `severity` field would be cleaner (T-008b option).
- Metric alerts:
  - J5 burst: the user-defined counter `imageopz-worker-5xx-<env>` above 30 in 1 h. The threshold is not 0 because
    a live-lease 503 is normal during duplicate deliveries.
  - jobs queue backlog: built-in `cloudtasks.googleapis.com/queue/depth` above 10 for 15 min (before the 1 h
    stale-PENDING horizon, section 2).
  - User-defined log-based metrics are chargeable as custom metrics; the first 150 MiB per billing account is free
    [R37], and one counter is far below that.
- Logs: 50 GiB/project/month free, then $0.50/GiB with 30 days retention included [R37]. Expected volume is well
  under 1 GiB.
- Budget: `imageopz imageopz-prod-4821`, 10 USD, project-scoped, alerts at 50/90/100 %. Budgets are free and alert
  only ("doesn't automatically cap ... spending") [R38]. Spend-cap budgets for Cloud Run exist (they pause the
  service, enforcement "isn't instant") [R39]; Q8.
- Supabase Free pauses after 1 week of inactivity [R20]. Prod traffic plus the 5-min sweep (DB queries) should count
  as activity; unverified, watch the Supabase dashboard in week 1.

## 10. Cost (portfolio traffic, per month)
Assumptions (prod): ~9k API requests (incl. status polling), ~900 image jobs at ~30 s on 2 vCPU / 4 GiB, ~300 cold
starts, a few payment events. Staging is mostly idle (schedules paused). Prices: Tier 1 [R2]; several pricing pages
render client-side, so their numbers come from search snippets of the official URLs. Recheck in the calculator.

| Item | Free tier | Estimate | Cost |
|---|---|---|---|
| Cloud Run services (request-based) | 180k vCPU-s, 360k GiB-s, 2M requests per billing account [R2] | worker 54k vCPU-s / 108k GiB-s; api ~3.3k vCPU-s; payments ~0 | $0 |
| Maintenance calls on the worker (Q1, request-based) | same service free tier | 8,640 sweeps + 720 cleanups per month; ~1 s each on a warm 2 vCPU / 4 GiB instance = ~19k vCPU-s / 38k GiB-s; up to ~94k / 188k if every tick cold-starts (unverified) | $0 (worker total stays under 180k vCPU-s / 360k GiB-s) |
| Cloud Run jobs | instance-based, 1-min minimum [R2] | migrate only, ~0 | $0 |
| Cloud Tasks | 1M operations [R40] | < 10k | $0 |
| Cloud Scheduler | 3 jobs per billing account, $0.10/job after; paused jobs billed [R41] | 4 jobs | $0.10 |
| Artifact Registry | 0.5 GB [R2], ~$0.10/GiB-month after [R42]; same-region pulls free [R42] | ~1 GB after cleanup | ~$0.05 |
| Secret Manager | 6 active versions, 10k accesses [R2]; $0.06/version/location [R28] | 18 versions (9 x 2 env); < 10k accesses (read at instance start; no per-tick job starts after Q1) | ~$0.75 |
| Logging / Monitoring | 50 GiB per project; alerting free until 2027-09 [R37] | < 1 GiB, 1 counter | $0 |
| Internet egress (worker -> R2 uploads, -> Supabase) | 1 GiB free only within North America [R43] | ~2-3 GB to R2 | ~$0.30 at $0.12/GiB [R44] |
| Total | | | ~$1.2 |

Cost guards: max-instances on every service (2-3); worker concurrency 1 and jobs-queue concurrency below the worker
max; bounded retries on both queues (section 2) and scheduler retries 0; jobs `--max-retries` 0/1 and task timeouts;
min-instances 0 everywhere; request-based billing for services; staging schedules paused; Artifact Registry cleanup
policy; 10 USD budget alerts and the kill switch (section 0). The largest variable is now worker cold starts on the
5-min sweep; check it in the first billing report (item 23).

## 11. Go-live checklist (BLOCKER = must be done before prod traffic)
| # | Item | Owner/task |
|---|---|---|
| 1 | BLOCKER: Python Dockerfile (none exists today) and the CI build/push/deploy workflow (section 6) | T-008b |
| 2 | BLOCKER: U15 replace the account-wide outputs token with one scoped to `imageopz`, revoke the old one, re-run the scope check | owner |
| 3 | BLOCKER: queues created with the J3 retry config (`40-queues.sh`) | owner (scripts) |
| 4 | BLOCKER: Scheduler -> `/internal/maintenance/{sweep,cleanup}` created and observed answering 200 once in each env (U2, Q1) | owner (scripts, T-008b2) |
| 5 | BLOCKER: `alembic upgrade head` via `imageopz-migrate-<env>` (session pooler, table owner); `tests/test_rls_deny_all.py` intent checked on Supabase (RLS on every table); Data API does not expose `payments` | owner |
| 6 | BLOCKER: all secret versions added; `SECRET_KEY` strong and per env | owner |
| 7 | BLOCKER: R2 CORS for the staging origin (staging bucket) and U3 lifecycle rules | owner (Cloudflare) |
| 8 | BLOCKER: staging end-to-end: presign, PUT from the browser, complete, worker DONE, download; a duplicate delivery (503 then 200); the worker's own OIDC check passes behind Cloud Run IAM (no 401); a test-mode order activates Pro once; webhook replay is `duplicate` | owner + T-008b |
| 9 | BLOCKER: LemonSqueezy webhook URL switched to the prod payments ingress, signing secret matches, checkout re-enabled only after #8 | owner |
| 10 | BLOCKER: worker memory measured with a 50 MB / ~179 MP image in staging; adjust `--memory`/`--cpu` (U4) | T-008b |
| 11 | BLOCKER: `supabase db dump` of prod before the first and every later prod migration (Free plan has no backups [R33]) | owner |
| 12 | `SERVICE_ROLE` route split (DONE T-008b1); `EXPOSE_API_DOCS` (A1); SECRET_KEY check scoped to the api | T-008b2 |
| 13 | S4: cache Google OIDC certs in `app/utils/internal_auth.py` | T-008b |
| 14 | DONE T-008b1 (H1): `/health` generic liveness, `/readyz` DB check with 503 generic body | - |
| 15 | X-Forwarded-For: confirm in staging what Cloud Run puts in XFF (`IpFingerprint` takes the first entry, spoofable); fix before relying on IP for quotas (T-002 risk, U1) | T-008b |
| 16 | U1: risk accepted with cheap bounds (Q6, section 0); no limiter at go-live | owner (decided 2026-10-17) |
| 17 | Budget + notification channel + alert policies (`90-observability.sh`); each policy seen firing once in staging (forced error, failed job) | owner |
| 18 | WIF deployers (`80-wif.sh`) and the GitHub Environment `production` with the owner as required reviewer | owner |
| 19 | Explicit `timeout=` on Cloud Tasks `create_task` (T-007 finding 7) | T-008b |
| 20 | DONE T-008b1 (C1): session cookie `Secure` in every environment except development | - |
| 21 | J2: cleanup with an unconfigured outputs client marks rows deleted; config here always sets it; fix in code later | backlog |
| 22 | Supabase limits for the owner role (T-005 follow-up): `idle_in_transaction_session_timeout` (e.g. 60 s; the worker holds no transaction while processing, T-007) and a `statement_timeout`; set via a reviewed SQL statement, not in app code | owner + T-008b |
| 23 | Billing report after the first month vs section 10 (ADR-002 validation); revisit Q1 and sizing | owner |
| 24 | Database secrets use the bare `postgresql://` form (section 5) | owner |

Script order (each first with DRY_RUN=1, then DRY_RUN=0): 00, 10, 20 (per env), 30 (per env, then add the secret
values), 40, 50 with EXECUTE_MIGRATE=1, 60, 65, 70, 80, 90. Staging first; prod after item 8.

## 12. Rollback runbook
1. Stop the damage: `gcloud tasks queues pause imageopz-jobs-<env> --location=asia-northeast1` (deliveries wait;
   resume later); payments queue likewise. Pause the scheduler jobs if maintenance misbehaves. The kill switch
   (section 0) does all of this in one command.
2. Services: send traffic back to the previous revision (no rebuild):
   `gcloud run services update-traffic imageopz-<svc>-<env> --region=asia-northeast1 --to-revisions=<prev-revision>=100`
   for api, worker, payments. Then pin CI to the previous digest (`prod-<sha>` tag) so the next deploy does not
   re-roll the bad image.
3. Migrate job: `gcloud run jobs deploy imageopz-migrate-<env> --image=<previous digest>` (same flags as `50-jobs.sh`).
4. Schema: normally nothing (expand/contract keeps the previous release compatible). If a migration itself is wrong:
   owner takes `supabase db dump`, then
   `gcloud run jobs execute imageopz-migrate-<env> --region=asia-northeast1 --args=downgrade,<revision> --wait`
   (needs runWithOverrides, owner only), then rolls services back as in step 2. Downgrades that drop columns lose data.
   Restore from the dump if needed.
5. Payments: if the ingress or the payments queue is broken, point the LemonSqueezy webhook URL back to
   `https://<api>/api/v1/webhooks/lemonsqueezy` (ADR-001 rollback path, same service function, idempotent per order).
6. Resume queues; watch the alerts and the queue depth; record the incident in registers.md.

## 13. Open questions (owner)
Answered 2026-10-17; decisions in section 0. The original questions are kept below for the record.
- Q1 Scheduled work: keep Cloud Run Jobs at 15 min / 6 h (as scripted), or move sweep and cleanup to
  `POST /internal/maintenance/{sweep,cleanup}` on the worker, triggered by Scheduler with OIDC (same pattern as
  Cloud Tasks; request-based billing, inside the free tier, any cadence)? Recommended: the endpoint. It needs an
  ADR-005 amendment and T-008b code. The CLIs stay for manual runs, and migrate stays a job.
- Q2 API URL: run.app (recommended now) or a custom domain (domain mapping Preview, or a load balancer)?
- Q3 Origins: confirm the prod UI origin(s); choose the stable staging UI origin (Vercel branch domain) and the
  Supabase redirect URLs.
- Q4 Staging R2: separate buckets `imageopz-staging` / `imageopz-uploads-staging` with their own scoped tokens
  (recommended), or reuse prod buckets with a key prefix?
- Q5 Prod sizing: worker 2 vCPU / 4 GiB and max 3 instances; api max 3; payments max 2. Confirm after the item-10
  measurement.
- Q6 U1: accept the presign abuse risk at go-live (bounded by max-instances and R2 lifecycle) or block on a limiter?
- Q7 Staging Supabase: a second free project (recommended; 2 active projects allowed) vs shared? The 2-project limit
  counts every free project the owner's organization already has.
- Q8 Spend cap: add a Cloud Run spend-cap budget (pauses services when hit) or keep alert-only?
- Q9 Payment events that exhaust 24 h of retries: manual replay procedure (re-send from the LemonSqueezy dashboard
  hits the ingress, which dedupes as `duplicate` and does NOT re-enqueue). Needs a replay path in T-008b or a
  longer/unlimited retry duration (up to the 31-day retention).
- Q10 Org policy: confirm the project has no organization policy blocking `allUsers` (domain restricted sharing);
  read-only gcloud could not run here.
- Q11 J1 refund policy for infrastructure failures (credits stay spent today); payments follow-up, not a go-live
  blocker.

## References (read 2026-10-16)
- [R1] Cloud Run locations, Tier 1 includes asia-northeast1: https://docs.cloud.google.com/run/docs/locations
- [R2] Cloud Run pricing (free tiers, Tier 1 prices, "jobs are billed at the Instance-based billing rate ... with a minimum of 1 minute", free tier per billing account): https://cloud.google.com/run/pricing (snippet); free program: https://docs.cloud.google.com/free/docs/free-cloud-features
- [R3] Request timeout: https://docs.cloud.google.com/run/docs/configuring/request-timeout
- [R4] Concurrency: https://docs.cloud.google.com/run/docs/about-concurrency
- [R5] CPU and memory: https://docs.cloud.google.com/run/docs/configuring/services/cpu , https://docs.cloud.google.com/run/docs/configuring/services/memory-limits
- [R6] Billing settings: https://docs.cloud.google.com/run/docs/configuring/billing-settings
- [R7] Max/min instances: https://docs.cloud.google.com/run/docs/configuring/max-instances , https://docs.cloud.google.com/run/docs/configuring/min-instances
- [R8] gcloud run deploy: https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy ; digests: https://docs.cloud.google.com/run/docs/deploying
- [R9] Jobs: https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy , https://docs.cloud.google.com/run/docs/configuring/task-timeout , https://docs.cloud.google.com/run/docs/configuring/max-retries , https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/execute
- [R10] RetryConfig: https://docs.cloud.google.com/tasks/docs/reference/rest/v2/RetryConfig ; queue flags: https://docs.cloud.google.com/sdk/gcloud/reference/tasks/queues/create
- [R11] Artifact Registry cleanup policies: https://docs.cloud.google.com/artifact-registry/docs/repositories/cleanup-policy
- [R12] Scheduler -> Cloud Run job: https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule
- [R13] Scheduler auth and locations: https://docs.cloud.google.com/scheduler/docs/http-target-auth , https://docs.cloud.google.com/scheduler/docs/locations , https://docs.cloud.google.com/sdk/gcloud/reference/scheduler/jobs/create/http
- [R14] Cloud Tasks quotas: https://docs.cloud.google.com/tasks/docs/quotas
- [R15] Ingress: https://docs.cloud.google.com/run/docs/securing/ingress
- [R16] Deterministic URL: https://docs.cloud.google.com/run/docs/triggering/https-request
- [R17] Audiences: https://docs.cloud.google.com/run/docs/configuring/custom-audiences
- [R18] Service-to-service auth: https://docs.cloud.google.com/run/docs/authenticating/service-to-service
- [R19] OIDC actAs and service agent: https://docs.cloud.google.com/tasks/docs/reference/rest/v2/OidcToken , https://docs.cloud.google.com/tasks/docs/creating-http-target-tasks , https://docs.cloud.google.com/tasks/docs/reference-access-control
- [R20] Supabase pricing (Free: 500 MB, pause after 1 week, 2 active projects): https://supabase.com/pricing
- [R21] Cloud Run IAM roles: https://docs.cloud.google.com/run/docs/reference/iam/roles
- [R22] Deploy permissions: https://docs.cloud.google.com/run/docs/deploying
- [R23] Cloud Run logging: https://docs.cloud.google.com/run/docs/logging
- [R24] Service identity: https://docs.cloud.google.com/run/docs/securing/service-identity
- [R25] WIF for deployment pipelines: https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines
- [R26] Federation support by product: https://docs.cloud.google.com/iam/docs/federated-identity-supported-services ; auth action: https://github.com/google-github-actions/auth
- [R27] Secret access and stdin versions: https://docs.cloud.google.com/secret-manager/docs/manage-access-to-secrets , https://docs.cloud.google.com/sdk/gcloud/reference/secrets/versions/add
- [R28] Secret Manager pricing: https://cloud.google.com/secret-manager/pricing (snippet)
- [R29] `--env-vars-file`: https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy
- [R30] Supabase compute (Nano: 60 connections, 200 pooler clients): https://supabase.com/docs/guides/platform/compute-and-disk
- [R31] Supabase connecting (ports, IPv4 pooler, no prepared statements in transaction mode): https://supabase.com/docs/guides/database/connecting-to-postgres
- [R32] Supavisor FAQ: https://supabase.com/docs/guides/troubleshooting/supavisor-faq-YyP5tI
- [R33] Supabase backups (Free: export with `db dump`): https://supabase.com/docs/guides/platform/backups
- [R34] Domain mapping: https://docs.cloud.google.com/run/docs/mapping-custom-domains
- [R35] R2 CORS: https://developers.cloudflare.com/r2/buckets/cors/
- [R36] R2 lifecycle: https://developers.cloudflare.com/r2/buckets/object-lifecycles/ , https://developers.cloudflare.com/workers/wrangler/commands/r2/
- [R37] Observability pricing: https://cloud.google.com/products/observability/pricing (snippet); alert cost: https://docs.cloud.google.com/monitoring/alerts/cost-control
- [R38] Budgets: https://docs.cloud.google.com/billing/docs/how-to/budgets , https://cloud.google.com/billing/v1/pricing
- [R39] Spend caps: https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps
- [R40] Cloud Tasks pricing: https://cloud.google.com/tasks/pricing (snippet)
- [R41] Cloud Scheduler pricing: https://cloud.google.com/scheduler/pricing (snippet)
- [R42] Artifact Registry pricing: https://cloud.google.com/artifact-registry/pricing (snippet); same-region egress: https://docs.cloud.google.com/artifact-registry/docs/repositories
- [R43] Cloud Run egress allowance: https://docs.cloud.google.com/run/docs/jobs-cost-optimization
- [R44] Network pricing: https://cloud.google.com/vpc/network-pricing (snippet)
"(snippet)": the page renders client-side; the figure came from a search snippet of that official URL and should be
rechecked on the live page or in the pricing calculator.
