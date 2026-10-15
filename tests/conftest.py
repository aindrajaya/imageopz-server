import sys
import os
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Ensure project root is importable when pytest is run from elsewhere.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Test isolation (T-006b incident, registers.md): before any app import, settings must not read the developer .env
# and every external-service variable gets a dummy value, so even a module imported at collection time (before
# test_env) cannot hold real credentials. test_env below sets the per-test values.
os.environ["IMAGEOPZ_ENV_FILE"] = ""
for _key in ("INTERNAL_AUTH_MODE", "INTERNAL_AUTH_ALLOWED_EMAILS"):  # unset: tests opt in via monkeypatch
    os.environ.pop(_key, None)
os.environ.update({
    "DATABASE_URL": "sqlite://",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "REDIS_PASSWORD": "dummy-redis-password",
    "SECRET_KEY": "test-secret-key",
    "R2_ACCOUNT_ID": "dummy-account",
    "R2_ACCESS_KEY_ID": "dummy-r2-key",
    "R2_SECRET_ACCESS_KEY": "dummy-r2-secret",
    "R2_BUCKET_NAME": "dummy-outputs",
    "R2_CUSTOM_DOMAIN": "https://cdn.invalid",
    "R2_UPLOADS_BUCKET": "dummy-uploads",
    "R2_UPLOADS_ACCESS_KEY_ID": "",
    "R2_UPLOADS_SECRET_ACCESS_KEY": "",
    "LEMONSQUEEZY_SECRET": "test-lemonsqueezy-secret",
    "LEMONSQUEEZY_API_KEY": "",
    "LEMONSQUEEZY_STORE_ID": "",
    "LEMONSQUEEZY_VARIANT_ID": "",
    "LEMONSQUEEZY_REDIRECT_URL": "https://app.invalid/thanks",
    "LEMONSQUEEZY_TEST_MODE": "True",
    "SUPABASE_JWKS_URL": "http://example.invalid/jwks",
    "SUPABASE_JWT_ISSUER": "https://supabase.test/auth/v1",
    "SUPABASE_JWT_AUDIENCE": "authenticated",
    "SUPABASE_AUTH_URL": "https://supabase-auth.invalid/auth/v1",
    "SUPABASE_ANON_KEY": "dummy-anon-key",
    "RESEND_API_KEY": "dummy-resend-key",
    "INTERNAL_AUTH_SHARED_SECRET": "dummy-internal-secret",
    "INTERNAL_AUTH_AUDIENCE": "https://internal.invalid",
})

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ExternalNetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch, request):
    """
    Any DNS lookup or socket connect to a non-local host raises ExternalNetworkBlocked naming the host. Attempts are
    also recorded and fail the test at teardown, so code that swallows the exception cannot hide the attempt.
    """
    import socket

    attempts = []

    def check(host):
        h = host.decode() if isinstance(host, bytes) else str(host)
        if h not in _LOCAL_HOSTS:
            attempts.append(h)
            raise ExternalNetworkBlocked(f"external network blocked in tests: {h}")

    real_getaddrinfo, real_connect, real_connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex
    real_gethostbyname, real_sendto = socket.gethostbyname, socket.socket.sendto

    def getaddrinfo(host, *args, **kwargs):
        if host is not None:
            check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self, address):
        if isinstance(address, tuple):
            check(address[0])
        return real_connect(self, address)

    def connect_ex(self, address):
        if isinstance(address, tuple):
            check(address[0])
        return real_connect_ex(self, address)

    def gethostbyname(host):
        check(host)
        return real_gethostbyname(host)

    def sendto(self, data, *args):
        address = args[-1]
        if isinstance(address, tuple):
            check(address[0])
        return real_sendto(self, data, *args)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", gethostbyname)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield attempts
    if attempts:
        pytest.fail(f"{request.node.nodeid} attempted external connections: {sorted(set(attempts))}", pytrace=False)


@pytest.fixture(scope="session")
def test_env(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("imageopz_tests")
    db_path = tmp_dir / "test.db"

    # Default: temp SQLite file. If TEST_DATABASE_URL is set (postgresql[+psycopg]://...), a fresh
    # database is created on that server, migrated with `alembic upgrade head`, and dropped afterwards.
    pg_admin_url = os.environ.get("TEST_DATABASE_URL")
    pg_db_name = None
    if pg_admin_url:
        import uuid
        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        admin = make_url(pg_admin_url.replace("postgresql://", "postgresql+psycopg://", 1))
        pg_db_name = f"imageopz_test_{uuid.uuid4().hex[:10]}"
        conninfo = admin.set(drivername="postgresql").render_as_string(hide_password=False)
        with psycopg.connect(conninfo, autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(pg_db_name)))
        database_url = admin.set(database=pg_db_name).render_as_string(hide_password=False)
    else:
        database_url = f"sqlite:///{db_path}"

    # Ensure settings pick up safe test values (settings instantiates on import).
    os.environ["ENVIRONMENT"] = "development"
    os.environ["DEBUG"] = "False"
    os.environ["DATABASE_URL"] = database_url
    os.environ["DATABASE_ECHO"] = "False"
    os.environ["SECRET_KEY"] = "test-secret-key"

    # Force Redis to be absent; tests should not depend on a running redis-server.
    os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"

    # Keep CORS simple for tests
    os.environ["CORS_ALLOW_ORIGINS"] = "http://localhost"
    os.environ["CORS_ALLOW_CREDENTIALS"] = "False"

    # Webhook secret for signature verification tests
    os.environ["LEMONSQUEEZY_SECRET"] = "test-lemonsqueezy-secret"
    # Disable outbound LemonSqueezy API calls during tests (avoid reading .env values).
    os.environ["LEMONSQUEEZY_API_KEY"] = ""
    os.environ["LEMONSQUEEZY_STORE_ID"] = ""
    os.environ["LEMONSQUEEZY_VARIANT_ID"] = ""

    # Uploads bucket: never use real credentials from .env; tests inject an in-memory UploadStore (fake_store).
    os.environ["R2_UPLOADS_ACCESS_KEY_ID"] = ""
    os.environ["R2_UPLOADS_SECRET_ACCESS_KEY"] = ""

    # Supabase auth defaults for tests (individual tests may override/mok)
    os.environ["SUPABASE_JWKS_URL"] = "http://example.invalid/jwks"
    os.environ["SUPABASE_JWT_ISSUER"] = "https://supabase.test/auth/v1"
    os.environ["SUPABASE_JWT_AUDIENCE"] = "authenticated"

    if pg_db_name:
        # Build the schema exactly as production will: via Alembic, not create_all.
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        cfg.attributes["url"] = database_url  # don't rely on settings import order
        command.upgrade(cfg, "head")

    yield {"tmp_dir": tmp_dir, "db_path": db_path}

    if pg_db_name:
        try:
            import app.models as models
            if models.db.engine is not None:
                models.db.engine.dispose()
        except Exception:
            pass
        with psycopg.connect(conninfo, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(pg_db_name)))


@pytest.fixture()
def client(test_env):
    # Reload modules to re-bind settings/db to the test env vars.
    import app.config as config
    import app.models as models
    import app.main as main

    importlib.reload(config)
    importlib.reload(models)
    importlib.reload(main)

    # Ensure DB is initialized for the new engine/url.
    models.db.init_db()

    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def fake_store(client, monkeypatch):
    """In-memory uploads bucket used by the upload routes, sweeper and worker (no network)."""
    from app.services import upload_store
    from tests.upload_fakes import FakeUploadStore

    store = FakeUploadStore()
    monkeypatch.setattr(upload_store, "get_upload_store", lambda: store)
    return store
