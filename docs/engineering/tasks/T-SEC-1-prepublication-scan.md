# T-SEC-1 - Pre-publication scan (read-only)
Baseline main@0130a52 (clean tree). gitleaks 8.30.1 (installed with brew). No history rewrite, no edits besides this file. No secret value printed; scans used --redact.

## 1. Secrets
| Scan | Command | Scope | Result |
|---|---|---|---|
| History | `gitleaks git --redact --log-opts="--all" .` | 74 commits, all refs incl. local `backup-before-history-sync`, `origin/*` | **no leaks found** |
| Working tree | `gitleaks dir --redact .` | tracked + untracked + ignored files | 4 hits, all in `.env` (see below) |
| Files ever added | `git log --all --diff-filter=A` filtered for .env/.db/.pem/.key/secret/credential | all history | only `.env.example`; no `.env`, no SQLite DB, no keys ever committed |

Working-tree findings (classification):
| Rule | File:line | Tracked? | In history? | Class |
|---|---|---|---|---|
| jwt | .env:42 | no (gitignored) | no | real local credential, **not published** |
| jwt | .env:46 | no (gitignored) | no | same |
| generic-api-key | .env:31 (R2 secret access key) | no | no | same |
| generic-api-key | .env:57 (Resend API key) | no | no | same |
These are real values in the owner's local `.env`; they are safe while `.env` stays ignored. Recommendation: no rotation required for publication. Rotate only if `.env` was ever shared, backed up to a synced folder, or the offline VPS copy is not under your control. Nothing in the repo is a TRUE finding; there are no LIKELY-FALSE findings either.

## 2. PII in tracked files
Tracked files under issues/ (4), docs/ (28), tests/ (17); JSON/CSV/log fixtures: only `docs/openapi.json` (generated spec). No .csv/.log/.db tracked.
| Check | Result |
|---|---|
| Emails (HEAD) | 20x example.com (tests/fixtures), 5 service-account placeholders (`@p.iam...`, `@proj.iam...`), 3x `n***@i***.com` = the project's own noreply sender (`.env.example`, `app/config.py`, `docs/production-runbook.md`). No customer emails. |
| Emails (all history, added lines) | only the same noreply sender plus example.com/test.com |
| Phone numbers | none |
| IP addresses | HEAD: none. History: `10.0.0.5` (x4, private range), `131.0.0.0` (version-like). No public IPs |
| LemonSqueezy / Supabase IDs | none real: store/variant IDs are config names only; `<project-ref>` placeholder; UUIDs are fixtures/sample job IDs |
| `test.md` (root, tracked) | sample API output with job UUIDs and a screenshot filename ("Screenshot 2024-11-30 ..."); no emails/IPs. Owner note: low risk, but delete or move if not wanted |
| Commit metadata | 74 commits authored by "Arista Indrajaya" / "aindrajaya" with a personal gmail address (public in git history once published) |
No real customer data found.

## 3. Publication checklist (report only)
| Item | Finding |
|---|---|
| LICENSE | **absent** (no LICENSE file). Public repo without a license = all rights reserved; add one if you want reuse |
| Private infrastructure in tracked files | `nginx.conf` and `issues/r2-custom-domain.notes-7may2026.md` name `<api-host>`, `<assets-host>`, a Cloudflare Worker `<worker-name>`, and the letsencrypt paths; `.env.example` CORS lists `imageopz.aindrajaya.my.id`; `docs/systemd/*` and `docs/production-runbook.md` describe the old VPS layout. Hostnames are public DNS names, not secrets, but they reveal infra and a second project name. README itself has no private IPs/hosts |
| `.env.example` | placeholders only (`your-secret-key`, `re_your_api_key`, empty IDs). One oddity: a stray line 87 `<stray token>` (committed in 62c451e). Not a valid KEY=VALUE and not matched by gitleaks; **owner: confirm it is not a real password fragment**; remove it either way |
| `.vscode/` | tracked at one point in history (spellright dictionary etc.); no secrets seen |
| GitHub Actions secrets referenced | none (`secrets.*` not used by any workflow) |

## 4. Branch protection note
- `.github/workflows/payments-go.yml` has `paths` filters on push and pull_request (`services/payments-go/**`, `alembic/**`, the workflow file). If any of its jobs (`go`, `integration`, `docker`) is marked required, PRs not touching those paths will wait forever. Do not require them, or drop the filter.
- `.github/workflows/backend-tests.yml`: no path filter (see `on:` above); its checks (`pytest (sqlite)`, `pytest (postgres)`) are safe to require.

## 5. Decision summary for the owner
**Outcome A for secrets and PII: history is clean, no real credentials or customer data in any ref.** Safe to publish as is, after these small, non-blocking owner choices:
1. Confirm/remove the stray `<stray token>` line in `.env.example` (if it is a real password, it is in history -> treat as case B for that value: rotate it; removing the line from HEAD does not remove it from history).
2. Decide whether the `<parent-domain>` hostnames, systemd/nginx docs and `test.md` should stay public (infra disclosure only). If not, removing them in a normal commit is enough unless you want them out of history too.
3. Add a LICENSE.
4. Be aware the git author email (gmail) becomes public.
5. Keep `.env` ignored; the local `.env` holds live R2 and Resend keys.
If you later choose history cleanup (only needed for items 1-2): `git filter-repo` (destructive, rewrites SHAs, breaks open PR refs) or a fresh single-commit snapshot repo; for this repo the snapshot is simpler and safe, at the cost of losing history. Not recommended unless item 1 turns out to be a real secret.

Owner command: git add docs/engineering/tasks/T-SEC-1-prepublication-scan.md && git commit -m "docs(security): pre-publication scan report"

## Resolution (T-PUB-1)
Line 87 confirmed by the owner as not a secret and deleted; hostnames above redacted in the current tree; VPS-era files removed; LICENSE (all rights reserved) added. History still contains the old hostnames and the stray line (no rewrite, by decision).
