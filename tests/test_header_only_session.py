"""
Q2-B (DEPLOY-PLAN section 7): the UI on another site than the run.app API sends X-Session-Id and no cookies
(VITE_IMAGEOPZ_SEND_COOKIES=false). The whole anonymous flow must work without the imageopz_sid cookie.
"""
import uuid
from datetime import datetime, timedelta
from http.cookiejar import DefaultCookiePolicy

import pytest

from tests.upload_fakes import FakeEnqueuer, png_bytes


@pytest.fixture()
def no_cookies(client):
    """The client never stores a cookie, and every request is checked to carry no Cookie header."""
    client.cookies.clear()
    jar = client.cookies.jar  # the setter would copy the jar and drop its policy, so set the policy in place
    jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    sent = []

    def check(request):
        sent.append(request.url.path)
        assert "cookie" not in request.headers, request.url

    client.event_hooks["request"].append(check)
    yield sent
    client.event_hooks["request"].remove(check)
    jar.set_policy(DefaultCookiePolicy())


@pytest.fixture()
def enqueuer(monkeypatch):
    from app.services import job_enqueuer

    fake = FakeEnqueuer()
    monkeypatch.setattr(job_enqueuer, "get_enqueuer", lambda: fake)
    return fake


def _h(sid):
    return {"X-Session-Id": sid}


def _seed_pro_user(email):
    from app.models import db, User, UsersQuota

    s = db.get_session()
    try:
        user = User(email=email)
        s.add(user)
        s.flush()
        s.add(UsersQuota(id=f"quota_{uuid.uuid4().hex[:8]}", user_id=user.id, session_id=f"old_{uuid.uuid4().hex[:8]}",
                         is_pro=True, pro_expiry_date=datetime.utcnow() + timedelta(days=30), pro_verified=True))
        s.commit()
    finally:
        s.close()


def test_anonymous_flow_works_with_the_header_and_no_cookies(client, fake_store, enqueuer, no_cookies):
    sid = f"sess_hdr_{uuid.uuid4().hex[:8]}"

    me = client.get("/api/v1/session/me", headers=_h(sid))
    assert me.status_code == 200, me.text
    assert me.json()["session_id"] == sid and me.json()["credits_used"] == 0

    data = png_bytes()
    pre = client.post("/api/v1/uploads/presign", headers=_h(sid),
                      json={"files": [{"name": "a.png", "size": len(data), "content_type": "image/png"}]})
    assert pre.status_code == 201, pre.text
    f = pre.json()["files"][0]
    fake_store.put(f["object_key"], data, "image/png")  # the browser's PUT to R2

    assert client.get("/api/v1/session/me", headers=_h(sid)).json()["credits_used"] == 1

    done = client.post("/api/v1/uploads/complete", headers=_h(sid), json={"job_ids": [f["job_id"]]})
    assert done.status_code == 200, done.text
    assert done.json()["jobs"] == [{"job_id": f["job_id"], "status": "pending", "reason": None}]
    assert enqueuer.calls == [f["job_id"]]

    st = client.get(f"/api/v1/status/{f['job_id']}", headers=_h(sid))
    assert st.status_code == 200 and st.json()["status"] == "pending"

    # Another session id (another browser) cannot complete this session's job.
    other = client.post("/api/v1/uploads/complete", headers=_h(f"sess_other_{uuid.uuid4().hex[:8]}"),
                        json={"job_ids": [f["job_id"]]})
    assert other.status_code == 404

    rec = client.post("/api/v1/session/reconcile", headers=_h(sid))
    assert rec.status_code == 200 and rec.json()["session_id"] == sid

    out = client.post("/api/v1/session/logout", headers=_h(sid))
    assert out.status_code == 200
    new_sid = out.json()["session_id"]
    assert new_sid and new_sid != sid
    fresh = client.get("/api/v1/session/me", headers=_h(new_sid)).json()
    assert fresh["session_id"] == new_sid and fresh["credits_used"] == 0
    assert set(no_cookies) >= {"/api/v1/session/me", "/api/v1/uploads/presign", "/api/v1/uploads/complete",
                               "/api/v1/session/reconcile", "/api/v1/session/logout"}


def test_restore_binds_pro_to_the_header_session_without_cookies(client, no_cookies):
    email = f"pro_{uuid.uuid4().hex[:8]}@example.com"
    _seed_pro_user(email)
    sid = f"sess_hdr_restore_{uuid.uuid4().hex[:8]}"

    res = client.post("/api/v1/session/restore", headers=_h(sid), json={"email": email})
    assert res.status_code == 200, res.text
    assert res.json()["session_id"] == sid

    me = client.get("/api/v1/session/me", headers=_h(sid)).json()
    assert me["is_pro"] is True and me["session_id"] == sid
    # Without the header the same (cookie-less) browser is a different, non-Pro session.
    assert client.get("/api/v1/session/me").json()["is_pro"] is False


def test_requests_without_header_or_cookie_get_a_fresh_session_id_in_the_body(client, no_cookies):
    a = client.get("/api/v1/session/me").json()["session_id"]
    b = client.get("/api/v1/session/me").json()["session_id"]
    assert a and b and a != b  # the UI must persist the id it sends (localStorage); the server cannot
