# ADR-004 Payment event idempotency and internal auth
Status: accepted (owner, 2026-10-15)
## Context
ADR-001 R1-R5. LemonSqueezy payloads carry no per-delivery id (docs: meta.event_name, data.type, data.id only).
## Decision
- Dedupe key: unique (provider, event_name, object_type, object_id) in `processed_payment_events`
  = ("lemonsqueezy", meta.event_name, data.type, data.id). `order:created` is canonicalised to `order_created`.
  data.type falls back to "unknown" when absent.
- Unprocessable authentic events (after signature / internal auth passed): missing meta.event_name or data.id,
  an event other than order_created / order_refunded, a wrong payload shape (meta, data, attributes, custom_data not
  objects; email/session/ip fields not strings), invalid JSON or invalid UTF-8. Both routes answer 200
  `{"status": "ignored"}`, log a WARNING with event_name (truncated) and a reason code (never the payload or customer
  email) and perform no database writes. Authentication failures stay 401; only retriable failures (database
  unavailable) are 5xx with a generic body.
- The dedupe row is inserted first, in the same transaction as every effect; a unique violation returns "duplicate"
  with no state change; any other failure rolls back everything (R4) and returns a generic 500 (R5).
- Refund (R2): `order_refunded` sets orders.status = "refunded" and revokes the grant of that order: the quota row
  recorded in the new nullable `orders.user_quota_id` gets is_pro=false, pro_verified=false, pro_expiry_date=NULL,
  unless another paid order points at the same quota row. Paid checkout intents of the order become "refunded" so
  session rehydration cannot restore Pro. Partial refunds are treated like full refunds (LemonSqueezy sends one event
  type for both); revisit if partial refunds matter.
- R3: only order_created / order_refunded are applied; every other event is ignored, so an existing order status is
  never overwritten (no paid -> unknown) and no "unknown" orders are created. A paid or refunded order never re-activates (also covers order_created arriving after order_refunded).
- Internal auth (`INTERNAL_AUTH_MODE`): `shared_secret` (X-Internal-Token vs INTERNAL_AUTH_SHARED_SECRET,
  hmac.compare_digest); `oidc` (Bearer Google ID token via google-auth, audience INTERNAL_AUTH_AUDIENCE, verified email
  in INTERNAL_AUTH_ALLOWED_EMAILS); unset/unknown refuses everything. All refusals are 401.
## Consequences
- Unprocessable authentic events are acknowledged with 200 "ignored" instead of 4xx. Reason: the senders retry on any
  non-2xx (LemonSqueezy and Cloud Tasks), and a payload that can never be applied would be retried and alerted on
  forever, while unrelated event types (subscriptions, test pings) are legitimately sent to the same URL. The warning
  log with a reason code keeps them visible. Before, the public route answered 500 or processed an activation with no
  stable id. Signature verification now runs over the raw bytes so an undecodable body with a valid signature is
  ignored rather than failing with 500.
- `order:created` is normalised to `order_created` for the dedupe key and an already paid or refunded order never
  re-activates, so the alias and out-of-order delivery cannot double-grant.
- A refund marks the order's paid checkout intents "refunded" (new CheckoutIntentStatus member; string column, no DDL),
  so session/me and restore rehydration cannot bring Pro back.
- New column `orders.user_quota_id`: nullable FK to users_quota.id, ON DELETE SET NULL, named
  fk_orders_user_quota_id_users_quota. Refund revokes every quota row carrying the entitlement: the order's own quota plus
  every quota linked to the order's user (user_id, set by restore/rehydration), unless another paid order backs the same
  quota or user. Never matched by ip_hash or session_id alone.
- The dedupe row is claimed with INSERT ... ON CONFLICT DO NOTHING RETURNING as the first write (Postgres and SQLite).
Python stays the single writer. Rows in processed_payment_events are never deleted (small, audit value).
## Rollback
Downgrade 0002 drops the table and column; the public webhook keeps working through the same service.
