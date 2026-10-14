# ADR-001 Payments ingress in Go (LemonSqueezy webhooks)
Status: proposed (defaults stated 2026-10-15; owner did not object; owner to accept)
## Context
T-000: the FastAPI webhook writes orders, users, users_quota, checkout_intents, jobs.expiry_date; users_quota also has
other FastAPI writers (session restore, credit usage). Replay re-activated Pro; refunds were ignored; commits were
non-atomic. A Go service is wanted as a production-grade proof of Go on GCP.
## Options
| Option | Summary | Main risk |
| A | Go ports the full activation and writes the main tables | Duplicated matching logic (ip_hash, Supabase link) drifts; second writer of users_quota |
| B | Go writes orders directly, then calls the API | Still a second writer on shared tables |
| C | Go = ingress: verify X-Signature, dedupe deliveries in its own `payments` schema, enqueue via Cloud Tasks to the API internal endpoint | Extra hop; two services to run |
## Decision
C. Python remains the single writer of users/users_quota/orders.
Requirements: R1 activation once per order; R2 refund revokes Pro, status "refunded"; R3 non-activation events never
overwrite an existing status; R4 one transaction per event; R5 no exception text in 500s.
Internal auth: OIDC ID token from Cloud Tasks (audience + allowed service-account email) in prod; shared secret
locally; fail closed when unset. `payments` schema tables have RLS enabled.
## Consequences
Defense in depth (Go dedupes deliveries; Python enforces idempotent activation). Cloud Tasks gives retries.
## Observable surface
LemonSqueezy only sees 2xx/401 from the ingress URL. Internal endpoint contract: defined in the T-004 handoff.
## Rollback / removal path
Point the LemonSqueezy webhook URL back to the API's public route (kept as fallback, same service function).
## Validation
T-004 AC1–AC8; T-005 tests (signature, dedupe, enqueue, retry semantics).
## Revisit triggers
Ingress adds latency/cost without benefit; activation logic moves to Go after parity is proven.
