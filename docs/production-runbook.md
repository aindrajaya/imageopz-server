# Production runbook (Ubuntu 24.04 + systemd)

This repo is designed to run as **two systemd services** on a 1 vCPU / 1GB RAM VPS:

- `imageopz-api` (FastAPI/HTTP only; `uvicorn --workers 1`)
- `imageopz-worker` (Redis queue worker + cleanup scheduler in the same process)

## 1) Prereqs

- Python 3.10+ (Ubuntu 24.04 ships Python 3.12; recommended: venv)
- Redis (local or managed, required for queue)
- (Optional) Cloudflare R2 credentials (for public download URLs)

## 2) Recommended layout

- App directory: `/opt/imageopz/imageopz-server`
- Venv: `/opt/imageopz/venv`
- Env file: `/opt/imageopz/imageopz-server/.env`

## 3) Required env vars for production

Minimum:

- `ENVIRONMENT=production`
- `DEBUG=False`
- `SECRET_KEY=<strong random value>`
- `REDIS_URL=redis://127.0.0.1:6379/0` (or your managed Redis)
- `CORS_ALLOW_ORIGINS=https://imageopz.com` (comma-separated list)
- `LEMONSQUEEZY_SECRET=<webhook secret>` (required if you enable `/api/v1/webhooks/lemonsqueezy`)
- `LEMONSQUEEZY_API_KEY=<api key>` (required if you enable `/api/v1/checkout/lemonsqueezy`)
- `LEMONSQUEEZY_STORE_ID=<store id>` (required if you enable `/api/v1/checkout/lemonsqueezy` and don’t pass store_id in the request)
- `LEMONSQUEEZY_VARIANT_ID=<variant id>` (required if you enable `/api/v1/checkout/lemonsqueezy` and don’t pass variant_id in the request)

If using R2:

- `R2_ACCOUNT_ID=...`
- `R2_ACCESS_KEY_ID=...`
- `R2_SECRET_ACCESS_KEY=...`
- `R2_BUCKET_NAME=...`
- `R2_CUSTOM_DOMAIN=https://...`

If using Resend:

- `RESEND_API_KEY=re_...`
- `RESEND_FROM_EMAIL="ImageOpz <noreply@imageopz.com>"`

## 4) Install deps

From `/opt/imageopz/imageopz-server`:

- Create venv (recommended): `python3 -m venv /opt/imageopz/venv`
- Install: `/opt/imageopz/venv/bin/pip install -U pip && /opt/imageopz/venv/bin/pip install -r requirements.txt`

Redis (if local):

- Install: `sudo apt-get update && sudo apt-get install -y redis-server`
- Start/enable: `sudo systemctl enable --now redis-server`

## 5) systemd setup

Copy the unit files:

- `docs/systemd/imageopz-api.service` -> `/etc/systemd/system/imageopz-api.service`
- `docs/systemd/imageopz-worker.service` -> `/etc/systemd/system/imageopz-worker.service`

Then:

- Reload: `sudo systemctl daemon-reload`
- Enable: `sudo systemctl enable imageopz-api imageopz-worker`
- Start: `sudo systemctl start imageopz-api imageopz-worker`
- Logs: `journalctl -u imageopz-api -f` / `journalctl -u imageopz-worker -f`

Quick verification:

- Health: `curl -sS http://127.0.0.1:8000/health`
- Docs: `curl -I http://127.0.0.1:8000/docs`

## 6) Nginx (optional but recommended)

Put Nginx in front for TLS, gzip/brotli, request size limits, and better client timeouts.

## Notes

- Do **not** run background worker/scheduler inside the FastAPI lifespan when using `uvicorn --workers > 1`.
- On 1 vCPU / 1GB RAM, keep API workers at `1` and rely on the Redis worker for heavy image processing.

## Run Memory Monitor
```
sudo systemctl daemon-reload
sudo systemctl enable imageopz-memory-monitor
sudo systemctl start imageopz-memory-monitor
sudo systemctl status imageopz-memory-monitor
```