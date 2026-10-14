# T-004 - Idempotent order-activation service + internal events endpoint
Branch feat/payments-activation from main@da5ffac (HEAD da5ffac, clean tree at start). ADR-001 Status: accepted.
No commit made (owner commits). Final state: uncommitted working tree on da5ffac.

## Handoff
**Baseline -> final:** da5ffac -> da5ffac + uncommitted. SQLite 32 passed/4 skipped -> 56 passed/4 skipped.
Postgres 16.1 (throwaway, port 5433) 36 passed -> 60 passed.
**Dedupe key:** ("lemonsqueezy", meta.event_name, data.type, data.id). Confirmed from LemonSqueezy docs (event-types,
example-payloads): `meta.event_name`, `data.type` ("orders"), `data.id`; no delivery id in the payload.

| Criterion | Command | Result | Revision | Excerpt |
|---|---|---|---|---|
| Baseline | pytest -q / TEST_DATABASE_URL=... pytest -q | pass | da5ffac | 32 passed, 4 skipped / 36 passed |
| Tests-first | pytest tests/test_webhook_characterization.py before app changes | 3 failed as required | da5ffac | 3 failed, 9 passed |
| AC1-AC6 | pytest tests/test_payments_activation.py (+ characterization) | pass | da5ffac + wt | in 56 passed |
| AC7 SQLite | pytest -q | pass | da5ffac + wt | 56 passed, 4 skipped |
| AC7 Postgres | TEST_DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5433/postgres pytest -q | pass | da5ffac + wt | 60 passed |
| AC7 alembic | upgrade head; check; downgrade 0001; upgrade; downgrade base; upgrade; check (Postgres) | pass | da5ffac + wt | "No new upgrade operations detected." |
| AC7 SQLite migration | upgrade head; downgrade 0001 (batch ops) | pass | da5ffac + wt | ran without error |
| AC7 RLS | tests/test_rls_deny_all.py (covers processed_payment_events via Base.metadata); relrowsecurity = t | pass | da5ffac + wt | 3 passed |

## Changed assertions (AC8; all in tests/test_webhook_characterization.py, T-001 "currently" tests only)
1. `test_replay_order_created_currently_reactivates` -> `test_replay_order_created_is_single_activation`: credits stay 9 and expiry unchanged (R1).
2. `test_refund_event_does_not_revoke_pro` -> `test_refund_event_revokes_pro_and_marks_order_refunded`: is_pro False, status "refunded" (R2).
3. `test_exception_during_activation_returns_500_with_order_committed_but_not_activated` -> `..._leaves_no_partial_state`: no Order/event/user/quota rows; body `{"detail": "Webhook processing failed"}` (R4/R5). Its induction point moved to `app.services.payments.settings`.
No other existing test was edited.

## Deviations and decisions to review
- Added nullable `orders.user_quota_id` (FK users_quota) in 0002, not in the task text: R2 needs to know which quota an order granted (session/ip-only buyers have no user).
- Diff size: gross +~470 lines in app/ (service 320 of which ~220 is the activation code moved verbatim from the route; route -190). Net app change about +280. Not split: the route, service and table are interdependent.
- Behavior changes beyond R1-R5: payloads without event name or `data.id` now return 400 on the public route (before: 500 for bad JSON; activation without id used to proceed, and a top-level `id` fallback is gone). `order:created` alias is canonicalised for dedupe. An `order_created` for an already paid/refunded order never re-activates. A refund also flips the order's paid checkout intents to "refunded" (new CheckoutIntentStatus member, string column, no DDL).
- Order rows for non-order objects (e.g. subscription events, whose ids are not order ids) are still created, as before; an `unknown` placeholder is upgradeable by a later real order_created with the same id.
- Public success responses unchanged except: duplicate -> `{"success": true, "message": "Duplicate event ignored"}` (no token); 500 detail generic; malformed 400.

## INTERNAL ENDPOINT CONTRACT (for T-005)
- `POST /internal/payments/events`, body = original LemonSqueezy JSON bytes (Content-Type application/json).
- Auth per INTERNAL_AUTH_MODE: `shared_secret` -> header `X-Internal-Token: <INTERNAL_AUTH_SHARED_SECRET>`; `oidc` -> `Authorization: Bearer <Google ID token>` with audience = INTERNAL_AUTH_AUDIENCE and a verified `email` in INTERNAL_AUTH_ALLOWED_EMAILS (comma-separated). Unset/unknown mode refuses all.
- Response codes (superseded by T-004c; Cloud Tasks retries any non-2xx):

| Code | Body | Meaning | Retry |
|---|---|---|---|
| 200 | `{"status": "processed"}` | applied | no |
| 200 | `{"status": "duplicate"}` | already applied, no state change | no |
| 200 | `{"status": "ignored"}` | authentic but unprocessable (missing event name / data.id, unhandled event type, wrong shape, invalid JSON/UTF-8); WARNING logged, no writes | no (acknowledged) |
| 401 | `{"detail": "Unauthorized"}` | missing/invalid token, wrong audience or email, mode unset or unknown | Cloud Tasks retries any non-2xx: fix config and tasks recover |
| 500 | `{"detail": "Payment event failed"}` | retriable failure (e.g. database unavailable); state fully rolled back | yes, safe to redeliver |

- Env: INTERNAL_AUTH_MODE, INTERNAL_AUTH_SHARED_SECRET, INTERNAL_AUTH_AUDIENCE, INTERNAL_AUTH_ALLOWED_EMAILS.

## Not run, and why
Real LemonSqueezy/Google calls, commit/push/deploy, remote DB (not authorized). OIDC verified only with a fake verifier; google-auth's real `verify_oauth2_token` path (fetches Google certs) is untested. Concurrent-delivery race was not exercised; it relies on the unique index (second insert blocks, then IntegrityError -> duplicate).

## Follow-ups
- google-auth needs `requests` at runtime; it is present only transitively (via resend). Pin explicitly or switch transport (T-008).
- Refund does not revoke quota copies created by session rehydration (`pro_source="session"`) or extend to Job expiry.
- Partial refunds are treated as full refunds (ADR-004).
- Existing local SQLite files lack `orders.user_quota_id` (legacy init_db DDL is debt; recreate the file).
- Other routes still return `str(e)` in 500 details (get_job_status etc.); out of scope.
- App import of `Order`/`OrderStatus` in routes/optimization.py may now be unused (cleanup, not done to keep diff tight).
- STATE.md baseline says `main@d249de6`; branch point is da5ffac (docs-only, no code change).


# T-004b - review fixes (same branch, uncommitted; final state = da5ffac + working tree)

| Criterion | Command | Result | Revision | Excerpt |
|---|---|---|---|---|
| Full suite SQLite | pytest -q | pass | da5ffac + wt | 61 passed, 4 skipped |
| Full suite Postgres 16.1 | TEST_DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5433/postgres pytest -q | pass | da5ffac + wt | 65 passed |
| alembic | upgrade head; check; downgrade 0001; upgrade; downgrade base; upgrade; check; heads | pass | da5ffac + wt | "No new upgrade operations detected."; head 0002 |
| SQLite migration | upgrade head; downgrade 0001 | pass | da5ffac + wt | no error |
| FK | psql confdeltype of fk_orders_user_quota_id_users_quota | n (SET NULL) | da5ffac + wt | test_orders_user_quota_id_is_nullable_fk_set_null |
| M1 test has teeth | removed begin_nested, ran the test on Postgres | failed as expected, file restored | da5ffac + wt | 1 failed |

1. R2 completeness: the new test (restore + /session/me into a second session, then refund) FAILED first (second row kept Pro). Rule now: revoke the order's own quota (orders.user_quota_id) plus every quota with users_quota.user_id == order.user_id; skipped if another paid order backs the same quota or user. Never by ip_hash or session_id alone. Test also checks /session/me is_pro False for both sessions and restore returns 403.
2. Duplicate path: `_claim_event` is the first write, INSERT ... ON CONFLICT DO NOTHING RETURNING (dialect insert for Postgres and SQLite); no row = duplicate. Test pre-inserts the row via SQL and delivers on both routes (run on both backends).
3. `requests==2.34.2` pinned in requirements.txt.
4. orders.user_quota_id: nullable FK, ondelete SET NULL (model and migration changed), named per convention; test added, plus refund of a never-activated order.
5. ADR-004 Consequences updated.
6. Counts above (SQLite 56 -> 61 passed; Postgres 60 -> 65).
7. Review: `git diff main...HEAD` is empty (nothing committed), so the subagent got the equivalent patch of the working tree (tracked diff vs HEAD plus untracked files as additions), the R1-R5 text and the protected behaviors from registers.md.

## Independent review findings (subagent, fresh context, verbatim)
MUST
- M1. Swallowed DB errors inside the transaction can commit nothing and still report success (R4, R1). Location: app/services/payments.py best-effort try/except around the checkout-intent update, the Job expiry update, and the ip_hash lookup. On Postgres, if any statement inside those blocks fails (lock timeout, deadlock, serialization failure, statement timeout), the DB transaction is aborted but the exception is swallowed. session.commit() on an aborted Postgres transaction does not raise; it silently rolls back. Outcome: the dedupe row, order and quota are all rolled back, yet _apply returns activated=True, the public route returns 200 "Pro activated" plus an access_token for a user_quota_id that does not exist, and the provider will not retry. Fix: wrap each best-effort block in `with session.begin_nested():` and catch outside it, or drop the swallowing, or check session.is_active before commit.
  -> FIXED: `_best_effort` context manager (SAVEPOINT) around the supabase link, ip_hash lookup, intent update and job expiry steps; test with a real failing SQL statement (fails without the savepoint on Postgres).

SHOULD
- S1. Concurrent refund and created on a pre-existing placeholder order can leave an order refunded while Pro stays active (R2). The dedupe key includes the event name, so order_created and order_refunded for the same order do not serialize on _claim_event. A placeholder order (status unknown) exists; T1 (created) activates and commits; T2 (refund) was blocked on the order row, then writes status=refunded from a stale snapshot where user_quota_id and user_id are None, and _revoke_for_refund returns early. Outcome: order "refunded", quota still Pro. The no-existing-order case is safe (unique constraint, 500, retry correct). Fix: load the order with .with_for_update(), or populate_existing(), or pg_advisory_xact_lock(hash(external_id)) after the claim.
- S2. Malformed-shape payloads return 500 and are retried forever instead of 400. _identify validates only meta and data; if data.attributes or meta.custom_data is a list/string, _apply raises AttributeError -> 500. Non-string user_email/session_id can hit a DB type error. Invalid UTF-8 body: json.loads(bytes) raises UnicodeDecodeError (a ValueError, not JSONDecodeError): the internal route answers 500 instead of 400 (the public route already failed in the signature check before this patch). Fix: validate attributes and custom_data as dicts, catch ValueError/UnicodeDecodeError as 400, coerce or reject non-string ids.
- S3. Public webhook now returns 400 for payloads without data.id (behavior change). The old route returned 200 for unknown events with no id, and even activated order_created with no external id; the old top-level data.get("id") fallback was dropped. LemonSqueezy would retry/alert on non-order events lacking data.id. Option: return 200 "ignored" for non-activation events without an id.
- S4. OIDC verification fetches Google certs on every request (google_requests.Request() with no cached session): added latency, and a Google cert outage causes 401s that Cloud Tasks retries. Fix: module-level Request with a CacheControl session. Auth logic itself (fail closed, audience, allowed email, email_verified, compare_digest) looked correct; no bypass found.
- S5. Legacy orders. Orders left status=paid by the old code, activated or not, never re-activate on retry, so old "committed as paid but not activated" rows cannot self-heal by redelivery. Orders with NULL user_quota_id fall back to user_id matching on refund (revokes every quota linked to that user). Worth a note or a backfill.

Checked, no findings (reviewer): R1 dedupe (first write, alias canonicalised, waits on concurrent claim), R3, R5, signature 401s and access_token, RLS on the new table, migration FK/index, String status columns need no enum migration, jobs.status untouched, matching logic copied verbatim, rehydration excludes REFUNDED intents.

## Follow-ups added by T-004b
S1-S5 above (not fixed; none breaches R1-R5 in the single-delivery cases tested). Owner decision needed on S3 (public 400 vs 200 ignored for id-less non-order events) before pointing LemonSqueezy at this build.
Owner commands unchanged (see task T-004 prompt); nothing was run for commit, push or merge.


# T-004c - webhook response semantics (owner decision on S3; same branch, uncommitted)
Both routes: after authentication, any event that cannot be applied returns 200 `{"status": "ignored"}`, logs a WARNING
(`event_name`, `reason`), and writes nothing. Reason codes: invalid_json, not_an_object, bad_shape, missing_event_name,
missing_object_id, unhandled_event. Public route body is exactly `{"status": "ignored"}` (no `success` key).

| Criterion | Command | Result | Revision | Excerpt |
|---|---|---|---|---|
| SQLite | pytest -q | pass | da5ffac + wt | 69 passed, 4 skipped |
| Postgres 16.1 | TEST_DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5433/postgres pytest -q | pass | da5ffac + wt | 73 passed |
| alembic | upgrade head; check; heads (no schema change in T-004c) | pass | da5ffac + wt | "No new upgrade operations detected."; 0002 (head) |

## Changed assertions (T-004c) and why
1. tests/test_webhook_characterization.py `test_unknown_event_stores_order_as_unknown_and_changes_no_quota` -> `test_unknown_event_is_ignored_and_writes_nothing`: an unhandled event now answers `{"status": "ignored"}` with no order, event or quota rows (was: stored "unknown" order, body "Webhook processed"). Reason: owner decision, "no database writes for ignored events".
2. tests/test_payments_activation.py `test_internal_malformed_is_400` and `test_public_malformed_is_400_not_500` (T-004 tests, not T-001): replaced by the `test_ignored_*` set (200 ignored, no writes, both routes). Reason: the 400 rule is superseded.
No other existing assertion changed. New tests cover: missing data.id, missing event name, attributes/custom_data/email wrong shape, invalid JSON, non-object JSON, invalid UTF-8 with valid signature, invalid UTF-8 with bad signature (still 401), unknown event on a paid order (status stays paid), database unavailable (500, generic body, no host/SQL text), auth failures on unprocessable bodies (401). Each runs through both routes.

## Other changes
- `WebhookValidator.verify_lemonsqueezy_signature` now accepts bytes as well as str (same HMAC; str input behaves as before). Needed so the signature is checked over the raw body when it is not valid UTF-8. This touches signature code, which T-004 listed out of scope; the owner decision requires the invalid-UTF-8 case, and behavior for existing inputs and all 401 cases is unchanged and tested.
- `OrderStatus.UNKNOWN` is no longer produced by the webhook (legacy rows may still carry it and can be upgraded by a real order_created).
- Closes review S2 and S3. Open: S1, S4, S5 and new S6 in docs/engineering/registers.md.
- Owner commands unchanged and not run.
