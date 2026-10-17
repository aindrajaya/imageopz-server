"""POST /internal/maintenance/{sweep,cleanup}: Cloud Scheduler -> worker (ADR-005 amendment), same functions as the CLIs."""
import uuid
from datetime import datetime, timedelta

import pytest

from tests.upload_fakes import FakeEnqueuer

TOKEN = "test-internal-token"
SWEEP = "/internal/maintenance/sweep"
CLEANUP = "/internal/maintenance/cleanup"
SECRET_ERROR = "connection to db.internal:6543 refused for owner"


@pytest.fixture()
def internal(monkeypatch):
    from app.utils import internal_auth

    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)


@pytest.fixture()
def enqueuer(monkeypatch):
    from app.services import job_enqueuer

    fake = FakeEnqueuer()
    monkeypatch.setattr(job_enqueuer, "get_enqueuer", lambda: fake)
    return fake


class _Outputs:
    def __init__(self, ok=True):
        self.ok = ok
        self.deleted = []

    def delete_optimized_image(self, key):
        self.deleted.append(key)
        return self.ok


@pytest.fixture()
def outputs(monkeypatch):
    from app.services import image_service

    out = _Outputs()
    monkeypatch.setattr(image_service, "r2_storage", out)
    return out


def _add_job(status, **cols):
    from app.models import db, Job, JobStatus

    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    values = dict(
        id=job_id, session_id="sess_maint", status=JobStatus[status], original_filename="a.png", original_size=10,
        quality_setting=50, created_at=now, expiry_date=now + timedelta(days=7), source_object_key=f"uploads/{job_id}",
        declared_size_bytes=10, declared_content_type="image/png", upload_expires_at=now + timedelta(minutes=30),
        credit_pool="free",
    )
    values.update(cols)
    s = db.get_session()
    try:
        s.add(Job(**values))
        s.commit()
    finally:
        s.close()
    return job_id


def _job(job_id):
    from app.models import db, Job

    s = db.get_session()
    try:
        return s.get(Job, job_id)
    finally:
        s.close()


def _post(client, path, token=TOKEN):
    return client.post(path, headers={"X-Internal-Token": token} if token else {})


# ---- auth ----

@pytest.mark.parametrize("path", [SWEEP, CLEANUP])
def test_maintenance_requires_internal_auth(client, monkeypatch, path):
    from app.utils import internal_auth

    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", None)  # unset: fail closed
    assert _post(client, path).status_code == 401
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "unknown")
    assert _post(client, path).status_code == 401
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    assert _post(client, path, token=None).status_code == 401
    assert _post(client, path, token="wrong").status_code == 401
    assert client.get(path).status_code in (401, 405)  # never 2xx on GET


@pytest.mark.parametrize("path", [SWEEP, CLEANUP])
def test_maintenance_oidc_allows_listed_scheduler_only(client, monkeypatch, enqueuer, outputs, fake_store, path):
    from app.utils import internal_auth

    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "oidc")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_audience", "https://worker.example.test")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_allowed_emails", "iz-scheduler@p.iam.gserviceaccount.com")
    claims = {"email": "evil@p.iam.gserviceaccount.com", "email_verified": True}
    monkeypatch.setattr(internal_auth, "_verify_oidc_token", lambda token, audience: claims)
    bearer = {"Authorization": "Bearer tok"}

    assert client.post(path, headers=bearer).status_code == 401
    claims["email"] = "iz-scheduler@p.iam.gserviceaccount.com"
    claims["email_verified"] = False
    assert client.post(path, headers=bearer).status_code == 401
    claims["email_verified"] = True
    assert client.post(path).status_code == 401  # no bearer
    assert client.post(path, headers={"X-Internal-Token": TOKEN}).status_code == 401  # shared secret not accepted in oidc mode
    assert client.post(path, headers=bearer).status_code == 200


def _set_auth(monkeypatch, mode):
    from app.utils import internal_auth

    s = internal_auth.settings
    monkeypatch.setattr(s, "internal_auth_shared_secret", TOKEN)
    monkeypatch.setattr(s, "internal_auth_audience", "https://worker.example.test")
    monkeypatch.setattr(s, "internal_auth_allowed_emails", "iz-scheduler@p.iam.gserviceaccount.com")
    monkeypatch.setattr(internal_auth, "_verify_oidc_token",
                        lambda token, audience: {"email": "evil@p.iam.gserviceaccount.com", "email_verified": True})
    monkeypatch.setattr(s, "internal_auth_mode", mode)


@pytest.mark.parametrize("path", [SWEEP, CLEANUP])
@pytest.mark.parametrize("mode,headers", [
    (None, {"X-Internal-Token": TOKEN}),
    ("shared_secret", {"X-Internal-Token": "wrong"}),
    ("oidc", {"Authorization": "Bearer tok"}),  # disallowed email
])
def test_rejected_maintenance_calls_change_nothing(client, fake_store, enqueuer, outputs, monkeypatch, path, mode, headers):
    _set_auth(monkeypatch, mode)
    upload = _add_job("UPLOADING", upload_expires_at=datetime.utcnow() - timedelta(seconds=1))
    pending = _add_job("PENDING", enqueued_at=datetime.utcnow() - timedelta(seconds=601))
    expired = _add_job("DONE", r2_object_key=f"optimized/{uuid.uuid4()}.avif", r2_url="https://cdn.invalid/k",
                       expiry_date=datetime.utcnow() - timedelta(days=1))

    assert client.post(path, headers=headers).status_code == 401
    assert _job(upload).status.name == "UPLOADING"
    assert _job(pending).status.name == "PENDING" and _job(pending).enqueue_attempts in (0, None)
    assert _job(expired).deleted_at is None
    assert enqueuer.calls == [] and outputs.deleted == [] and fake_store.deleted == []


# ---- sweep ----

def test_sweep_returns_counts_and_repeat_calls_are_idempotent(client, internal, fake_store, enqueuer):
    expired = _add_job("UPLOADING", upload_expires_at=datetime.utcnow() - timedelta(seconds=1))
    stale = _add_job("PENDING", enqueued_at=datetime.utcnow() - timedelta(seconds=601))

    res = _post(client, SWEEP)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["expired_uploads"] >= 1 and body["requeued"] >= 1
    assert body["enqueue_failures"] == 0 and set(body) == {"status", "expired_uploads", "requeued", "enqueue_failures", "failed"}
    assert _job(expired).status.name == "REJECTED"
    assert (stale, 1) in enqueuer.attempts

    again = _post(client, SWEEP)
    assert again.status_code == 200
    assert again.json() == {"status": "ok", "expired_uploads": 0, "requeued": 0, "enqueue_failures": 0, "failed": 0}
    assert [a for a in enqueuer.attempts if a[0] == stale] == [(stale, 1)]


def test_sweep_enqueue_failure_is_503_generic(client, internal, fake_store):
    stale = _add_job("PENDING", enqueued_at=datetime.utcnow() - timedelta(seconds=601))
    res = _post(client, SWEEP)  # JOB_ENQUEUE_MODE unset in tests: the enqueuer fails closed
    assert res.status_code == 503
    assert res.json() == {"status": "retry"}
    assert _job(stale).status.name == "PENDING"


def test_sweep_database_error_is_503_without_exception_text(client, internal, fake_store, enqueuer, monkeypatch):
    from app.services import maintenance

    def boom(*a, **k):
        raise RuntimeError(SECRET_ERROR)

    monkeypatch.setattr(maintenance, "expire_stale_uploads", boom)
    res = _post(client, SWEEP)
    assert res.status_code == 503
    assert res.json() == {"status": "retry"} and "6543" not in res.text


def test_sweep_without_uploads_bucket_still_releases_credits(client, internal, enqueuer, monkeypatch):
    from app.services import upload_store

    def not_configured():
        raise upload_store.UploadStoreNotConfigured()

    monkeypatch.setattr(upload_store, "get_upload_store", not_configured)
    expired = _add_job("UPLOADING", upload_expires_at=datetime.utcnow() - timedelta(seconds=1))
    res = _post(client, SWEEP)
    assert res.status_code == 200, res.text
    assert _job(expired).status.name == "REJECTED"


# ---- cleanup ----

def test_cleanup_returns_counts_and_repeat_calls_are_idempotent(client, internal, outputs):
    past = datetime.utcnow() - timedelta(days=1)
    key = f"optimized/{uuid.uuid4()}.avif"
    expired = _add_job("DONE", r2_object_key=key, r2_url="https://cdn.invalid/x", expiry_date=past)

    res = _post(client, CLEANUP)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok" and body["deleted"] >= 1 and body["failed"] == 0 and set(body) == {"status", "deleted", "failed"}
    assert _job(expired).deleted_at is not None and _job(expired).r2_url is None

    again = _post(client, CLEANUP)
    assert again.status_code == 200
    assert again.json() == {"status": "ok", "deleted": 0, "failed": 0}
    assert outputs.deleted.count(key) == 1


def test_cleanup_r2_failure_is_503_generic_and_retried_next_call(client, internal, outputs):
    outputs.ok = False
    job_id = _add_job("DONE", r2_object_key=f"optimized/{uuid.uuid4()}.avif", r2_url="https://cdn.invalid/z",
                      expiry_date=datetime.utcnow() - timedelta(days=1))
    res = _post(client, CLEANUP)
    assert res.status_code == 503 and res.json() == {"status": "retry"}
    assert _job(job_id).deleted_at is None
    outputs.ok = True
    assert _post(client, CLEANUP).status_code == 200
    assert _job(job_id).deleted_at is not None


def test_cleanup_database_error_is_503_without_exception_text(client, internal, outputs, monkeypatch):
    from app.services import maintenance

    def boom(*a, **k):
        raise RuntimeError(SECRET_ERROR)

    monkeypatch.setattr(maintenance, "cleanup_expired_outputs", boom)
    res = _post(client, CLEANUP)
    assert res.status_code == 503 and res.json() == {"status": "retry"} and "6543" not in res.text
