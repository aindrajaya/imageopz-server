# T-001 — Characterization tests + CI for the LemonSqueezy webhook

Branch: test/webhook-characterization (from main@bee0a98). No commit made. No changes under app/ or to requirements.txt.

## Scope delivered
- `tests/test_webhook_characterization.py` — 12 tests pinning current webhook behavior.
- `.github/workflows/backend-tests.yml` — Python 3.11, `pip install -r requirements.txt`, `pytest -q`, no secrets (also installs `libmagic1` via apt for python-magic).
- `docs/engineering/STATE.md`, `tasks/T-000-recon.md`, this file.

## Handoff

**Baseline -> final:** main@bee0a98 (uncommitted working tree on branch test/webhook-characterization). Tests: 15 passed -> 27 passed, 0 failed.

**Environment (per authorization amendment):**
- Interpreter: `/opt/homebrew/opt/python@3.11/bin/python3.11` = Python 3.11.2 (pre-existing Homebrew install; nothing installed besides the venv).
- Install: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv ~/.venvs/imageopz && ~/.venvs/imageopz/bin/pip install -r requirements.txt` (pins unchanged, install succeeded).
- `pip freeze | wc -l` = 53.
- CI uses 3.11 to match.

| Criterion | Command | Result | Revision | Excerpt |
|---|---|---|---|---|
| Baseline | `~/.venvs/imageopz/bin/pytest -q` | pass | bee0a98 | `15 passed` |
| AC1 scenarios 1-8 (+9) | `pytest tests/test_webhook_characterization.py` | pass | bee0a98 + uncommitted | `12 passed` |
| AC1 app/ untouched | `git diff --stat -- app/` | empty | bee0a98 + uncommitted | (no output) |
| AC2 existing tests unedited | `git diff --stat -- tests/` | empty (new file only) | same | (no output) |
| AC3 offline, temp DB | conftest temp SQLite + dead Redis; no imageopz.db or tmp/ created | pass | same | `ls imageopz.db tmp` -> not found |
| AC4 full suite on final diff | `~/.venvs/imageopz/bin/pytest -q` | pass | same | `27 passed` |

**Scenarios not covered:** none of 1-9. Scenario 9 is induced by monkeypatching `optimization.settings.pro_tier_expiry_days` to a non-int, which raises after the Order commit and before any user/quota write. It covers that failure point only, not a failure later in activation (e.g. between the activation writes and the final commit, or a `generate_token` failure, which happens after the final commit).

**Contradictions with T-000:** none that change the plan. Confirmed: replay resets credits and extends expiry; refund does not revoke Pro; unknown events store `unknown` and leave quota untouched; ip_hash-only path creates a user-less Pro quota; supabase conflict is logged and skipped.
Additional observations (new, minor):
- A refund event for an existing paid order overwrites that order's `status` to `unknown` and `event_name` to `order_refunded` (pinned in `test_refund_event_does_not_revoke_pro`).
- Failure responses echo the exception text in `detail` (pinned).
- Test DB is session-scoped and shared across tests, so tests use unique ids.

**Proposed next step:** T-002 — decide replay and refund semantics with the owner, then change them test-first by editing the "currently" tests deliberately; define column-level write ownership between FastAPI and Go.
