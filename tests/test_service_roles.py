"""SERVICE_ROLE route split (DEPLOY-PLAN section 3): api = public routes only, worker = /internal/* and health only."""
import pytest

TOKEN = "test-internal-token"
INTERNAL_POSTS = ("/internal/jobs/process", "/internal/payments/events", "/internal/maintenance/sweep",
                  "/internal/maintenance/cleanup")


@pytest.fixture()
def internal(monkeypatch):
    from app.utils import internal_auth

    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)


def _paths(app):
    return {getattr(r, "path", None) for r in app.routes}


def test_api_role_does_not_mount_internal_routes(make_client, internal):
    c = make_client(SERVICE_ROLE="api")
    for path in INTERNAL_POSTS:
        # A valid internal token proves the 404 comes from routing, not from auth.
        res = c.post(path, json={"job_id": "x"}, headers={"X-Internal-Token": TOKEN})
        assert res.status_code == 404, (path, res.status_code)
    assert not [p for p in _paths(c.app) if p and p.startswith("/internal")]
    assert c.get("/api/v1/session/me", headers={"X-Session-Id": "sess_role_api"}).status_code == 200
    assert c.get("/health").status_code == 200
    assert c.get("/readyz").status_code == 200
    assert c.get("/").status_code == 200


def test_worker_role_mounts_only_internal_and_health(make_client, internal):
    c = make_client(SERVICE_ROLE="worker")
    public = [p for p in _paths(c.app) if p and not p.startswith("/internal")]
    assert sorted(p for p in public if p not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")) == [
        "/health", "/readyz", "/version",
    ]
    for method, path in (("get", "/api/v1/session/me"), ("post", "/api/v1/uploads/presign"),
                         ("post", "/api/v1/webhooks/lemonsqueezy"), ("get", "/api/v1/status/x"), ("get", "/")):
        assert getattr(c, method)(path).status_code == 404, path
    assert c.get("/health").status_code == 200
    res = c.post("/internal/jobs/process", content=b"not json", headers={"X-Internal-Token": TOKEN})
    assert res.status_code == 200 and res.json() == {"status": "ignored"}


def test_worker_role_has_no_cors(make_client):
    c = make_client(SERVICE_ROLE="worker")
    res = c.options("/health", headers={"Origin": "http://localhost", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in res.headers


def test_all_role_mounts_both(make_client, internal):
    c = make_client(SERVICE_ROLE="all")
    assert c.get("/api/v1/session/me", headers={"X-Session-Id": "sess_role_all"}).status_code == 200
    res = c.post("/internal/jobs/process", content=b"not json", headers={"X-Internal-Token": TOKEN})
    assert res.status_code == 200


def test_default_role_is_all(client):
    from app.config import settings

    assert settings.service_role == "all"


@pytest.mark.parametrize("value", ["bogus", "", "public", "api,worker"])
def test_unknown_role_fails_closed(monkeypatch, value):
    from pydantic import ValidationError
    from app.config import Settings

    monkeypatch.setenv("SERVICE_ROLE", value)
    with pytest.raises(ValidationError):
        Settings()


def test_role_value_is_normalized(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("SERVICE_ROLE", " Worker ")
    assert Settings().service_role == "worker"


def test_public_openapi_is_the_same_for_api_and_all(make_client):
    all_schema = make_client(SERVICE_ROLE="all").get("/openapi.json").json()
    api_schema = make_client(SERVICE_ROLE="api").get("/openapi.json").json()
    assert api_schema == all_schema


def test_committed_openapi_matches_the_app(client):
    import json
    from pathlib import Path

    committed = json.loads((Path(__file__).resolve().parents[1] / "docs" / "openapi.json").read_text())
    assert client.app.openapi() == committed
