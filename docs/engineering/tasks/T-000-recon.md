# T-000 — Payment boundary reconnaissance (read-only)

Baseline: main@bee0a98. Date: 2026-10-14. Outcome: accepted.

## Facts
- FastAPI 0.115, SQLAlchemy 2.0, SQLite (WAL, 30s timeout), single backend. Deployed with systemd + Nginx; **no Docker**, no CI at the time.
- Webhook: `POST /api/v1/webhooks/lemonsqueezy` -> `app/routes/optimization.py:handle_lemonsqueezy_webhook`.
- Signature: `X-Signature`, HMAC-SHA256 hex over the raw body (decoded/re-encoded), `hmac.compare_digest`, fail-closed when secret unset (`app/utils/security.py:WebhookValidator`).
- Only `order_created` / `order:created` activate Pro. Other events with an id are stored as `Order(status=unknown)` and return 200.
- Writes: `orders`, `users`, `users_quota`, `checkout_intents`, `jobs.expiry_date`.
- Other writers of `users_quota` Pro fields: `app/routes/session.py` (restore / me rehydration), `app/routes/optimization.py` (rehydration, `pro_credits_used` increments on every optimize).
- Schema migrations: `create_all` + hand-written `ALTER TABLE` in `app/models.py:Database.init_db`; no Alembic.
- Nginx: `^/api/v1/webhooks` -> 127.0.0.1:8000 with `webhook_zone` 10r/m, burst 5.

## Findings (priority)
1. H: webhook touches 4 tables + jobs; matching logic (user_id > session_id > ip_hash intent) must be reproduced exactly.
2. H: replay re-activates (credits reset, expiry extended); no already-processed short-circuit.
3. H: FastAPI also writes Pro fields, so "Go owns, FastAPI reads only" does not hold today.
4. H: DDL lives only in FastAPI startup.
5. H: SQLite with a third writer needs busy_timeout/WAL/foreign_keys on the Go side.
6. H: no replay/refund/unknown/ip_hash tests, no CI.
7. M: refunds/cancellations ignored; refund overwrites paid order status with unknown.
8. M: non-atomic commits (Order commit, then activation commit); 500 echoes exception text.
9. M: ip_hash-only path creates a Pro quota with no user.
10. M: Nginx 10r/m limit could throttle LemonSqueezy retries.

## Open questions for the owner
Same-VPS resource headroom for Go; refund/cancel semantics; whether replay re-extension is intended; use of the `access_token` in the webhook response; fate of the ip_hash path; production `DATABASE_URL`; backup plan for the SQLite file.
