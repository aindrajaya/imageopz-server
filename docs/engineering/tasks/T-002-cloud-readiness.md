# T-002 — Cloud-readiness recon (GCP Cloud Run + Postgres), read-only

Status: accepted 2026-10-15 (REPORTED by owner). Recon was run at `b504211` (branch `test/webhook-characterization`); `app/` is unchanged since
except `app/models.py` and `app/config.py` (T-003). No repo artifact other than this file; the matrix below is a summary and was not re-verified on `main` line by line.

## Findings that drive the plan
| Concern | Evidence | Cloud Run compatible? | Change |
|---|---|---|---|
| Uploads on local disk shared by API and worker | `app/routes/optimization.py` (~374-384 write `{upload_dir}/{job_id}.upload`), `app/workers/job_worker.py:_get_file_data` | No | Direct-to-R2 presigned uploads (T-006) |
| Request size | Pro: 50MB x 30 images; Nginx `client_max_body_size 1600M`; Cloud Run HTTP/1 request limit 32 MiB (verified 2026-10-15 per Cloud Run quotas; no limit with HTTP/2) | No | Same as above |
| Redis list queue + BLPOP worker thread | `app/workers/job_worker.py:JobQueue`, `worker_runner.py` | Needs change | Cloud Tasks, remove Redis (T-007) |
| In-process cleanup scheduler | `app/workers/cleanup_scheduler.py` | Needs change | Cloud Scheduler + Cloud Run Job (T-008) |
| SQLite + startup DDL | `app/models.py:init_db` | No | Done in T-003 (Alembic, Postgres) |
| Nginx rate limits (`nginx.conf` zones) | no app-level limiter | Lost | Cloud Armor or app-level limiter (open) |
| Client IP from first `X-Forwarded-For` entry | `app/utils/security.py:IpFingerprint` | Needs change | Trusted-proxy fix (T-008) |
| Outputs on Cloudflare R2, no local file serving | `app/services/image_service.py:R2Storage` | Yes | Keep R2 |
| Supabase usage is auth only (JWKS) | `app/utils/supabase_auth.py` | Yes | DB via `DATABASE_URL` |
| `libmagic` system dependency | `app/utils/security.py` (python-magic), CI installs `libmagic1` | Needs change | Dockerfile (T-008) |

## Proposed process model (owner leaning to adopt, see ADR-002 status in STATE.md)
API Cloud Run service (no worker), worker as a Cloud Tasks HTTP target, Alembic migration as a Cloud Run Job, cleanup via Cloud Scheduler.

## Open questions raised then
Production data source (since recovered by the owner), GCS vs R2 (decided: keep R2), queue choice (decided: Cloud Tasks), upload strategy (decided: presigned),
Supabase pooler mode (decided: transaction 6543 for services, session 5432 for migrations), expected traffic and cold-start tolerance (UNKNOWN).
