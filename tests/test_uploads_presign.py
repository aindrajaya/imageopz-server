"""POST /api/v1/uploads/presign: tier validation before any write, atomic credit reservation, job creation."""
from datetime import datetime, timedelta

import pytest

PNG = "image/png"


def _presign(client, session_id, files, **extra):
    return client.post(
        "/api/v1/uploads/presign",
        json={"files": [{"name": n, "size": s, "content_type": t} for n, s, t in files], **extra},
        headers={"X-Session-Id": session_id},
    )


def _quota(session_id):
    from app.models import db, UsersQuota

    s = db.get_session()
    try:
        return s.query(UsersQuota).filter_by(session_id=session_id).first()
    finally:
        s.close()


def _jobs(session_id):
    from app.models import db, Job

    s = db.get_session()
    try:
        return s.query(Job).filter_by(session_id=session_id).all()
    finally:
        s.close()


def _seed_pro(quota_id, session_id, pro_credits_used=0):
    from app.models import db, UsersQuota

    s = db.get_session()
    try:
        s.add(UsersQuota(
            id=quota_id,
            session_id=session_id,
            ip_hash="ip_hash_" + quota_id,
            is_pro=True,
            pro_expiry_date=datetime.utcnow() + timedelta(days=10),
            pro_credits_used=pro_credits_used,
        ))
        s.commit()
    finally:
        s.close()


def test_presign_happy_path_creates_uploading_jobs_and_reserves_credits(client, fake_store):
    from app.config import settings

    before = datetime.utcnow()
    res = _presign(client, "sess_presign_ok", [("a.png", 1000, PNG), ("b.jpg", 2000, "image/jpeg")])
    assert res.status_code == 201, res.text
    body = res.json()

    assert len(body["files"]) == 2
    for f in body["files"]:
        assert f["object_key"] == f"uploads/{f['job_id']}"  # server-generated; no client filename in the key
        assert f["method"] == "PUT"
        assert f["upload_url"].startswith("https://")
        expires_at = datetime.fromisoformat(f["expires_at"])
        assert timedelta(seconds=settings.upload_url_ttl_seconds - 5) <= expires_at - before <= timedelta(seconds=settings.upload_url_ttl_seconds + 5)
    assert [f["headers"] for f in body["files"]] == [{"Content-Type": PNG}, {"Content-Type": "image/jpeg"}]
    complete_by = datetime.fromisoformat(body["complete_by"])
    assert timedelta(seconds=settings.upload_complete_window_seconds - 5) <= complete_by - before <= timedelta(seconds=settings.upload_complete_window_seconds + 5)

    assert [(p["key"], p["content_type"], p["expires_in"], p["content_length"]) for p in fake_store.presigned] == [
        (body["files"][0]["object_key"], PNG, settings.upload_url_ttl_seconds, 1000),
        (body["files"][1]["object_key"], "image/jpeg", settings.upload_url_ttl_seconds, 2000),
    ]

    jobs = {j.id: j for j in _jobs("sess_presign_ok")}
    assert set(jobs) == {f["job_id"] for f in body["files"]}
    a = jobs[body["files"][0]["job_id"]]
    assert a.status.name == "UPLOADING"
    assert (a.original_filename, a.declared_size_bytes, a.declared_content_type, a.credit_pool) == ("a.png", 1000, PNG, "free")
    assert a.source_object_key == body["files"][0]["object_key"]
    assert a.upload_expires_at is not None

    quota = _quota("sess_presign_ok")
    assert quota.credits_used == 2
    assert a.user_quota_id == quota.id
    assert res.cookies.get("imageopz_sid") == "sess_presign_ok"


def test_presign_stores_session_id_on_users_quota(client, fake_store):
    # Replaces test_optimize_session_id: quota is keyed by the session id, not only ip_hash.
    res = _presign(client, "session_test_presign_123", [("tiny.png", 10, PNG)])
    assert res.status_code == 201, res.text
    quota = _quota("session_test_presign_123")
    assert quota is not None
    assert quota.ip_hash is not None
    assert quota.is_pro is False


@pytest.mark.parametrize(
    "files, code, error",
    [
        ([("a.png", 10, PNG)] * 4, 429, "QUOTA_EXCEEDED"),  # free: max 3 files per request
        ([("big.png", 10 * 1024 * 1024 + 1, PNG)], 413, "FILE_TOO_LARGE"),
        ([("a.gif", 10, "image/gif")], 400, "UNSUPPORTED_TYPE"),
        ([("a.exe", 10, PNG)], 400, "UNSUPPORTED_TYPE"),
    ],
)
def test_presign_tier_violations_rejected_before_any_write(client, fake_store, files, code, error):
    sid = f"sess_violation_{code}_{len(files)}_{files[0][0]}"
    res = _presign(client, sid, files)
    assert res.status_code == code, res.text
    assert res.json()["error"] == error
    assert res.json()["success"] is False
    assert _jobs(sid) == []
    quota = _quota(sid)
    assert quota is None or (quota.credits_used or 0) == 0
    assert fake_store.presigned == []


def test_presign_free_credit_exhaustion_is_atomic(client, fake_store):
    assert _presign(client, "sess_free_credits", [("a.png", 10, PNG), ("b.png", 10, PNG)]).status_code == 201
    res = _presign(client, "sess_free_credits", [("c.png", 10, PNG), ("d.png", 10, PNG)])
    assert res.status_code == 429, res.text
    assert "Free tier quota exceeded" in res.json()["message"]
    assert _quota("sess_free_credits").credits_used == 2
    assert len(_jobs("sess_free_credits")) == 2
    assert len(fake_store.presigned) == 2


def test_presign_pro_allows_more_than_3_without_auth_token(client, fake_store):
    # Replaces test_pro_quota_without_token: webhook-activated Pro quota works without auth_token.
    _seed_pro("quota_presign_pro_1", "pro_presign_session_1")
    res = _presign(client, "pro_presign_session_1", [(f"tiny{i}.png", 10, PNG) for i in range(4)])
    assert res.status_code == 201, res.text
    assert {j.credit_pool for j in _jobs("pro_presign_session_1")} == {"pro"}
    assert _quota("pro_presign_session_1").pro_credits_used == 4


def test_presign_pro_size_limit_is_50mb(client, fake_store):
    _seed_pro("quota_presign_pro_size", "pro_presign_size")
    assert _presign(client, "pro_presign_size", [("big.png", 20 * 1024 * 1024, PNG)]).status_code == 201
    res = _presign(client, "pro_presign_size", [("huge.png", 50 * 1024 * 1024 + 1, PNG)])
    assert res.status_code == 413, res.text


def test_presign_pro_credit_cap_blocks_after_limit(client, fake_store):
    # Replaces test_pro_credit_cap.
    from app.config import settings

    _seed_pro("quota_presign_pro_cap", "pro_presign_cap", pro_credits_used=max(0, settings.pro_tier_max_credits - 1))
    assert _presign(client, "pro_presign_cap", [("tiny.png", 10, PNG)]).status_code == 201
    res = _presign(client, "pro_presign_cap", [("tiny2.png", 10, PNG)])
    assert res.status_code == 429, res.text
    assert "Pro credits exceeded" in res.text or "Remaining credits" in res.text
    assert _quota("pro_presign_cap").pro_credits_used == settings.pro_tier_max_credits


def test_presign_without_configured_store_writes_nothing(client):
    # No fake_store: the real store has no credentials in tests.
    res = _presign(client, "sess_no_store", [("a.png", 10, PNG)])
    assert res.status_code == 503, res.text
    assert res.json()["error"] == "UPLOADS_UNAVAILABLE"
    assert _jobs("sess_no_store") == []
    assert _quota("sess_no_store") is None


def test_presign_signing_failure_releases_reserved_credits(client, fake_store):
    fake_store.fail_presign = True
    res = _presign(client, "sess_presign_fail", [("a.png", 10, PNG), ("b.png", 10, PNG)])
    assert res.status_code == 503, res.text
    assert _quota("sess_presign_fail").credits_used == 0
    assert {(j.status.name, j.rejection_reason) for j in _jobs("sess_presign_fail")} == {("REJECTED", "presign_failed")}


@pytest.mark.parametrize("name", ["UPLOADING", "REJECTED"])
def test_new_job_statuses_are_stored_as_member_name(client, name):
    import uuid
    from sqlalchemy import text
    from app.models import db, Job, JobStatus

    job_id = str(uuid.uuid4())
    s = db.get_session()
    try:
        s.add(Job(id=job_id, session_id="sess_new_status", status=JobStatus[name]))
        s.commit()
    finally:
        s.close()
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}).scalar_one() == name


def test_uploads_return_generic_503_without_uploads_credentials_even_with_output_credentials(client, monkeypatch, caplog):
    # No fallback to the outputs-bucket token: only R2_UPLOADS_* may sign uploads.
    import logging
    from fastapi.testclient import TestClient
    from app.services import upload_store
    import app.main as main

    s = upload_store.settings
    monkeypatch.setattr(s, "r2_account_id", "acct")
    monkeypatch.setattr(s, "r2_access_key_id", "outputs-key")
    monkeypatch.setattr(s, "r2_secret_access_key", "outputs-secret")
    monkeypatch.setattr(s, "r2_uploads_access_key_id", "")
    monkeypatch.setattr(s, "r2_uploads_secret_access_key", "")
    monkeypatch.setattr(upload_store, "_store", None)

    generic = {"success": False, "error": "UPLOADS_UNAVAILABLE", "message": "Uploads are temporarily unavailable", "details": None}
    res = _presign(client, "sess_no_uploads_creds", [("a.png", 10, PNG)])
    assert (res.status_code, res.json()) == (503, generic)
    res = client.post("/api/v1/uploads/complete", json={"job_ids": ["x"]}, headers={"X-Session-Id": "sess_no_uploads_creds"})
    assert (res.status_code, res.json()) == (503, generic)
    assert _jobs("sess_no_uploads_creds") == []

    with caplog.at_level(logging.WARNING):
        with TestClient(main.app):
            pass
    assert any("R2 uploads bucket not configured" in r.getMessage() for r in caplog.records)
