# STATE — ImageOpz payments migration

## Baseline
- T-000 baseline: main@bee0a98.
- T-001 working branch: test/webhook-characterization @ bee0a98 + uncommitted changes (not committed).
- Backend: FastAPI + SQLAlchemy + SQLite (WAL), systemd + Nginx, no Docker.
- Test env: Python 3.11.2 venv at `~/.venvs/imageopz` (outside repo); run `~/.venvs/imageopz/bin/pytest -q`. Suite: 27 passed (15 baseline + 12 characterization).
- CI: `.github/workflows/backend-tests.yml` (Python 3.11), added in T-001, not yet run on GitHub.

## Payment boundary (see tasks/T-000-recon.md)
- Webhook: POST /api/v1/webhooks/lemonsqueezy -> app/routes/optimization.py:handle_lemonsqueezy_webhook
- Signature: X-Signature, HMAC-SHA256 hex of raw body, compare_digest (app/utils/security.py:WebhookValidator)
- Writes: orders, users, users_quota, checkout_intents, jobs.expiry_date
- Other writers of users_quota Pro fields: app/routes/session.py, app/routes/optimization.py
- Known gaps (now pinned by tests/test_webhook_characterization.py): replay re-activates, refunds ignored, non-atomic commits, 500 echoes exception text, ip_hash-only Pro path.

## Tasks
- T-000 recon: done (tasks/T-000-recon.md)
- T-001 characterization tests + CI: done, awaiting review/commit (tasks/T-001-characterization.md)

## Decisions pending
- Column-level write ownership between Go and FastAPI
- Replay / refund semantics; fate of ip_hash path
- Who owns DDL / migrations

## Next step
- Review and commit T-001; then T-002 (fix agreed behaviors test-first, edit the "currently" tests deliberately).
