# ADR-002 Hosting: Cloud Run + Supabase Postgres + R2 + Cloud Tasks
Status: accepted (owner, 2026-10-15)
## Context
VPS deleted; ImageOpz offline; no paying clients; budget ~$10/month GCP credit; deployment is a portfolio piece.
T-002: local-disk uploads shared by API and worker, Redis BLPOP worker, SQLite, Nginx-only rate limits, XFF trust.
## Options
| Option | Cost | Trade-off |
| Compute Engine VM, Docker Compose, lift and shift | always-on VM | fastest to live; ops burden; throwaway work |
| Cloud Run + Cloud SQL | db-f1-micro $0.0105/h (~$7.7/mo) + storage | all-GCP; consumes most of the credit; shared core has no SLA |
| Cloud Run + Supabase Postgres (chosen) | ~$0 at portfolio traffic | Free plan pauses after 1 week inactivity; 500 MB |
## Decision
Cloud Run services: API (no in-process worker), worker (HTTP handler fed by Cloud Tasks), Go payments ingress.
Cloud Run Job: `alembic upgrade head` before each rollout. Cloud Scheduler → Cloud Run Job for cleanup.
Storage: keep Cloudflare R2 for outputs; uploads go browser → R2 via presigned URLs (Cloud Run HTTP/1 request max
32 MiB). Redis removed. Supabase shared pooler transaction mode (6543) for services, session mode (5432) for the
migration job; app and migration job use the table-owner role (RLS invariant). min-instances 0, max-instances capped.
Secrets in Secret Manager; CI deploys via Workload Identity Federation; one image per SHA, promoted by digest.
## Consequences
Scale-to-zero cost; cold starts acceptable; lost Nginx rate limits until Cloud Armor or an app limiter.
## Revisit triggers
First paying client; Supabase pause or >400 MB; worker CPU cost; need for Cloud Armor.
## Validation
T-006..T-008 acceptance criteria; billing report after first month.

## Amendment 2026-10-15 - Region
Cloud Run services, Cloud Run Jobs and the Cloud Tasks queue run in asia-northeast1 (Tokyo). Reason: co-location with the Supabase project in ap-northeast-1 (Tokyo) keeps database round trips short, and asia-northeast1 is a Tier 1 Cloud Run pricing region. Region of the Supabase project is REPORTED (owner), not verifiable from the repo.
