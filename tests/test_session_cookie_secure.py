"""C1: the session cookie is Secure in every ENVIRONMENT except "development"."""
import pytest


@pytest.fixture()
def environment(client, monkeypatch):
    from app.routes import session as session_routes
    from app.services import quota_context

    def _set(value):
        # Route modules keep the settings object from their first import; patch every holder.
        for holder in {id(session_routes.settings): session_routes.settings,
                       id(quota_context.settings): quota_context.settings}.values():
            monkeypatch.setattr(holder, "environment", value)

    return _set


def _session_cookies(res):
    return [h for h in res.headers.get_list("set-cookie") if h.startswith("imageopz_sid=")]


def _calls(client):
    return [
        client.get("/api/v1/session/me", headers={"X-Session-Id": "sess_c1"}),
        client.post("/api/v1/session/logout"),  # clears, then sets (both helpers in app/routes/session.py)
        client.post("/api/v1/uploads/presign", json={"files": [{"name": "a.png", "size": 10, "content_type": "image/png"}]},
                    headers={"X-Session-Id": "sess_c1"}),
    ]


@pytest.mark.parametrize("env", ["staging", "production"])
def test_cookie_is_secure_outside_development(client, fake_store, environment, env):
    environment(env)
    for res in _calls(client):
        cookies = _session_cookies(res)
        assert cookies, res.request.url
        assert all("secure" in c.lower().split("; ") for c in cookies), cookies


def test_cookie_is_not_secure_in_development(client, fake_store, environment):
    environment("development")
    for res in _calls(client):
        cookies = _session_cookies(res)
        assert cookies, res.request.url
        assert not any("secure" in c.lower().split("; ") for c in cookies), cookies
