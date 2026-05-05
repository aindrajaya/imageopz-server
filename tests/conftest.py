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

    # Ensure settings pick up safe test values (settings instantiates on import).
    os.environ["ENVIRONMENT"] = "development"
    os.environ["DEBUG"] = "False"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
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

    yield {"tmp_dir": tmp_dir, "db_path": db_path, "upload_dir": upload_dir}


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
