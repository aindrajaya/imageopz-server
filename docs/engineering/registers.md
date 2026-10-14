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

## Protected behaviors
| Behavior | Enforced by |
|---|---|
| Webhook signature failures return 401 (invalid, missing header, secret unset) | `tests/test_webhook_characterization.py` |
| Successful public webhook response includes `access_token` | `tests/test_webhook_characterization.py`, `tests/test_webhook.py` |
| RLS deny-all on all app tables and `alembic_version` | `tests/test_rls_deny_all.py` (Postgres leg of CI) |
| `jobs.status` stored as member name (`PENDING`, ...) | `tests/test_job_status_storage.py` |
