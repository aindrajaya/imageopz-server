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

    # Store uploads under the temp dir so tests don't pollute the repo.
    upload_dir = tmp_dir / "uploads"
    os.environ["UPLOAD_DIR"] = str(upload_dir)

    # Webhook secret for signature verification tests
    os.environ["LEMONSQUEEZY_SECRET"] = "test-lemonsqueezy-secret"
    # Disable outbound LemonSqueezy API calls during tests (avoid reading .env values).
    os.environ["LEMONSQUEEZY_API_KEY"] = ""
    os.environ["LEMONSQUEEZY_STORE_ID"] = ""
    os.environ["LEMONSQUEEZY_VARIANT_ID"] = ""

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

    yield {"tmp_dir": tmp_dir, "db_path": db_path, "upload_dir": upload_dir}

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
