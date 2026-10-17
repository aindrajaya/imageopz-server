# Python image for the api and worker services and the migrate job (one image; DEPLOY-PLAN.md "Image strategy").
#   SERVICE_ROLE=api|worker picks the routes; the start command refuses anything else (no "all" in a deployed server).
#   The migrate job overrides the command: `alembic upgrade head` (deploy/gcloud/50-jobs.sh).
# Base digest resolved from Docker Hub on 2026-10-17: python:3.11.17-slim-trixie (= 3.11-slim) multi-arch index.
# Built and smoke-tested in CI only (.github/workflows/image.yml); Cloud Run needs no HEALTHCHECK (probes are set there).

FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
# libmagic for python-magic (upload MIME sniffing); trixie ships it as libmagic1t64 (no package named libmagic1).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1t64 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
# IMAGEOPZ_ENV_FILE="": settings never read a .env file in the container; configuration comes from the environment.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IMAGEOPZ_ENV_FILE="" \
    PORT=8080
USER 10001:10001
EXPOSE 8080
# --proxy-headers with uvicorn's default --forwarded-allow-ips (127.0.0.1): never "*", which makes uvicorn take the
# first (client-supplied) X-Forwarded-For entry. The client IP comes from TRUSTED_PROXY_HOPS in the app
# (app/utils/security.py:IpFingerprint), which reads the header itself.
CMD ["sh", "-c", "case \"${SERVICE_ROLE:-}\" in api|worker) ;; *) echo 'SERVICE_ROLE must be api or worker' >&2; exit 64 ;; esac; exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --proxy-headers"]
