"""Outputs are served by presigned GET (T-008c2, D1): /status signs a fresh URL per request for the job's owner; the
worker stores only the object key; the outputs bucket stays private."""
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_restore_only_login_policy import _make_token, _rsa_jwk_and_private_key
from tests.upload_fakes import png_bytes

SID = "sess_owner_dl"


class _Outputs:
    """Fake outputs store: upload and presign_get, no network."""

    def __init__(self, fail_presign=False):
        self.fail_presign = fail_presign
        self.signed = []

    def upload_optimized_image(self, data, original_filename, job_id):
        # A URL in the third slot (the pre-T-008c2 shape) must never be stored by the worker.
        return True, f"optimized/{job_id}.avif", f"https://cdn.invalid/optimized/{job_id}.avif"

    def presign_get(self, key, expires_in):
        if self.fail_presign:
            raise RuntimeError("signing failed")
        self.signed.append((key, expires_in))
        return f"https://fake-outputs.invalid/{key}?X-Amz-Expires={expires_in}&n={len(self.signed)}"


@pytest.fixture()
def outputs(client, monkeypatch):
    from app.services import image_service
    from app.workers import job_worker

    out = _Outputs()
    monkeypatch.setattr(image_service, "r2_storage", out)
    monkeypatch.setattr(job_worker, "r2_storage", out)
    return out


def _add_job(status="DONE", session_id=SID, quota_id=None, **cols):
    from app.models import db, Job, JobStatus

    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    values = dict(
        id=job_id,
        session_id=session_id,
        user_quota_id=quota_id,
        status=JobStatus[status],
        original_filename="a.png",
        original_size=10,
        quality_setting=50,
        created_at=now,
        expiry_date=now + timedelta(days=7),
        r2_object_key=f"optimized/{job_id}.avif" if status == "DONE" else None,
    )
    values.update(cols)
    s = db.get_session()
    try:
        s.add(Job(**values))
        s.commit()
    finally:
        s.close()
    return job_id


def _status(client, job_id, sid=SID, headers=None):
    h = dict(headers or {})
    if sid:
        h["X-Session-Id"] = sid
    res = client.get(f"/api/v1/status/{job_id}", headers=h)
    assert res.status_code == 200, res.text
    return res


def test_done_job_gets_a_fresh_presigned_url_on_every_call(client, outputs):
    job_id = _add_job()

    first = _status(client, job_id)
    second = _status(client, job_id)

    key = f"optimized/{job_id}.avif"
    assert outputs.signed == [(key, 900), (key, 900)]
    assert first.json()["r2_url"] and second.json()["r2_url"]
    assert first.json()["r2_url"] != second.json()["r2_url"]
    assert first.headers["cache-control"] == "no-store"


def test_ttl_comes_from_settings(client, outputs, monkeypatch):
    from app.routes import optimization

    monkeypatch.setattr(optimization.settings, "r2_output_url_ttl_seconds", 120)
    job_id = _add_job()
    assert "X-Amz-Expires=120" in _status(client, job_id).json()["r2_url"]
    assert outputs.signed == [(f"optimized/{job_id}.avif", 120)]


def test_session_cookie_identifies_the_owner_too(client, outputs):
    job_id = _add_job()
    client.cookies.set("imageopz_sid", SID)
    try:
        assert _status(client, job_id, sid=None).json()["r2_url"]
    finally:
        client.cookies.clear()


@pytest.mark.parametrize("status", ["UPLOADING", "PENDING", "PROCESSING", "FAILED", "REJECTED"])
def test_job_not_done_has_no_url(client, outputs, status):
    job_id = _add_job(status=status)
    assert _status(client, job_id).json()["r2_url"] is None
    assert outputs.signed == []


@pytest.mark.parametrize("sid", [None, "sess_someone_else"])
def test_foreign_anonymous_caller_gets_status_without_url(client, outputs, sid):
    job_id = _add_job()
    body = _status(client, job_id, sid=sid).json()
    assert (body["status"], body["r2_url"]) == ("done", None)
    assert outputs.signed == []


def test_expired_or_cleaned_job_has_no_url(client, outputs):
    past = datetime.utcnow() - timedelta(seconds=1)
    expired = _add_job(expiry_date=past)  # not yet swept by cleanup
    cleaned = _add_job(expiry_date=past, deleted_at=past)
    no_key = _add_job(r2_object_key=None)

    for job_id in (expired, cleaned, no_key):
        assert _status(client, job_id).json()["r2_url"] is None
    assert outputs.signed == []


def test_signing_failure_or_missing_outputs_store_answers_without_url(client, outputs, monkeypatch):
    from app.services import image_service

    job_id = _add_job()
    monkeypatch.setattr(image_service, "r2_storage", _Outputs(fail_presign=True))
    assert _status(client, job_id).json()["r2_url"] is None
    monkeypatch.setattr(image_service, "r2_storage", None)
    assert _status(client, job_id).json()["r2_url"] is None


def test_authenticated_owner_gets_url_and_foreign_account_gets_404(client, outputs, monkeypatch):
    from app.config import settings
    from app.models import db, User, UsersQuota
    from app.utils import supabase_auth

    jwk, private_key = _rsa_jwk_and_private_key()

    async def _fake_get_jwks():
        return {"keys": [jwk]}

    monkeypatch.setattr(supabase_auth._jwks_cache, "get_jwks", _fake_get_jwks)

    def token(sub):
        return _make_token(sub=sub, email=f"{sub}@example.com", private_key=private_key,
                           issuer=settings.supabase_jwt_issuer, audience=settings.supabase_jwt_audience or "authenticated")

    s = db.get_session()
    try:
        for sub in ("sb_dl_owner", "sb_dl_other"):
            s.add(User(id=f"user_{sub}", email=f"{sub}@example.com", supabase_user_id=sub))
            s.add(UsersQuota(id=f"quota_{sub}", user_id=f"user_{sub}"))
        s.commit()
    finally:
        s.close()
    # Uploaded from another browser: the account, not the session id, proves ownership.
    job_id = _add_job(session_id="sess_other_browser", quota_id="quota_sb_dl_owner")

    owner = _status(client, job_id, sid=None, headers={"Authorization": f"Bearer {token('sb_dl_owner')}"})
    assert owner.json()["r2_url"]
    other = client.get(f"/api/v1/status/{job_id}", headers={"Authorization": f"Bearer {token('sb_dl_other')}"})
    assert other.status_code == 404
    assert outputs.signed == [(f"optimized/{job_id}.avif", 900)]


def test_worker_stores_the_key_and_no_permanent_url(client, fake_store, outputs, monkeypatch):
    from app.utils import internal_auth
    from tests.test_job_processing import TOKEN, _deliver, _job, _uploaded_job

    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)
    job_id = _uploaded_job(fake_store)

    assert _deliver(client, job_id).json() == {"status": "done"}
    job = _job(job_id)
    assert (job.status.name, job.r2_object_key, job.r2_url) == ("DONE", f"optimized/{job_id}.avif", None)


def test_r2_storage_signs_a_sigv4_get_for_the_outputs_bucket(client):
    """Real boto3 signing (local computation, no request) with the dummy outputs credentials from conftest."""
    from app.services.image_service import R2Storage

    url = R2Storage().presign_get("optimized/abc.avif", 900)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.hostname == "dummy-account.r2.cloudflarestorage.com"
    assert parsed.path == "/dummy-outputs/optimized/abc.avif"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"] and query["X-Amz-Expires"] == ["900"]
    assert query["X-Amz-Credential"][0].startswith("dummy-r2-key/")


def test_r2_storage_upload_returns_no_public_url(client):
    from app.services.image_service import R2Storage

    class _S3:
        def __init__(self):
            self.puts = []

        def put_object(self, **kwargs):
            self.puts.append(kwargs)

    storage = R2Storage()
    storage.s3_client = _S3()
    assert storage.upload_optimized_image(png_bytes(), "a.png", "job-1") == (True, "optimized/job-1.avif", None)
    assert storage.s3_client.puts[0]["Bucket"] == "dummy-outputs"
