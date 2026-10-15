# Registers

Updated 2026-10-15 by Claude Code (task T-DOCS-1).

## Lessons
| Date | Lesson | Rule |
|---|---|---|
| 2026-10-14 | VPS destroyed before a verified backup; the prod DB and `.env` were later recovered by the owner. | Before decommissioning a host, back up and verify a restore. Secrets live in Secret Manager, not host files. |

## Debt
| Item | Where | Note |
|---|---|---|
| SQLite legacy DDL path in `init_db` | `app/models.py` (`TODO(debt)`) | Remove once Postgres is the default for local dev. |
| `datetime.utcnow` (naive timestamps, deprecated) | `app/models.py`, `app/routes/*`, workers | Clean up in a dedicated task. |

## Risks
| Id | Risk | Fix |
|---|---|---|
| S1 | A concurrent order_created and order_refunded for the same pre-existing order do not serialize on the dedupe key; the refund can read a stale order (no quota link) and leave the order "refunded" while Pro stays active. | Load the order with `.with_for_update()` in `app/services/payments.py:_apply` (or an advisory lock per order id). Add a Postgres test. |
| S6 | Refunding one of several paid orders for the same user keeps Pro (another paid order backs it) but does not recompute expiry or the credit window from the remaining order. | Decide the rule with the owner (keep latest expiry vs recompute from remaining paid orders) in a payments follow-up task. |

## Follow-ups
| Id | Item | Tag |
|---|---|---|
| S4 | Cache Google OIDC certs for internal auth (`app/utils/internal_auth.py` builds a fresh `google_requests.Request()` per call, fetching certs every time). Use a module-level Request with a cached session. | T-008 |
| G1 | Go pool (8 conns) x Cloud Run instances must fit the Supabase pooler limit; set `--concurrency` and `--max-instances` accordingly. | T-008 |

## Protected behaviors
| Behavior | Enforced by |
|---|---|
| Webhook signature failures return 401 (invalid, missing header, secret unset) | `tests/test_webhook_characterization.py` |
| Successful public webhook response includes `access_token` | `tests/test_webhook_characterization.py`, `tests/test_webhook.py` |
| RLS deny-all on all app tables and `alembic_version` | `tests/test_rls_deny_all.py` (Postgres leg of CI) |
| `jobs.status` stored as member name (`PENDING`, ...) | `tests/test_job_status_storage.py` |

## T-008 checklist
- Region: asia-northeast1 for Cloud Run services, Cloud Run Jobs and the Cloud Tasks queue (ADR-002 amendment).
- Run `alembic upgrade head` as a Cloud Run Job through the Supabase session pooler (5432), as the table-owner role.
- Owner verifies in the Supabase dashboard that the Data API does not expose the `payments` schema (only `public` should be exposed).
- G1: size the Go pool (8) x Cloud Run instances against the Supabase pooler limit; set `--concurrency` and `--max-instances`.
- S4: cache Google OIDC certs in `app/utils/internal_auth.py`.
- Ingress and API OIDC settings (audience, allowed emails), queue and service accounts, Secret Manager, LemonSqueezy webhook URL switch (see tasks/T-005-payments-go-ingress.md).
