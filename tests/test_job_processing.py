"""Job processing via Cloud Tasks (ADR-005): enqueuer requests, the internal worker endpoint, lease, U8 sweeper, cleanup CLI."""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from tests.upload_fakes import FakeEnqueuer, png_bytes

TOKEN = "test-internal-token"
PROCESS = "/internal/jobs/process"
QUEUE = "projects/p/locations/asia-northeast1/queues/jobs"


class _Output:
    def __init__(self, ok=True):
        self.ok = ok
        self.uploaded = []

    def upload_optimized_image(self, data, original_filename, job_id):
        if not self.ok:
            return False, None, "connection reset"
        self.uploaded.append(job_id)
        return True, f"optimized/{job_id}.avif", f"https://cdn.invalid/optimized/{job_id}.avif"


@pytest.fixture()
def internal(monkeypatch):
    from app.utils import internal_auth

    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)


@pytest.fixture()
def output(monkeypatch):
    from app.workers import job_worker

    out = _Output()
    monkeypatch.setattr(job_worker, "r2_storage", out)
    return out


def _add_job(status="PENDING", data=None, quota_id=None, pool="free", **cols):
    from app.models import db, Job, JobStatus

    job_id = str(uuid.uuid4())
    s = db.get_session()
    try:
        s.add(Job(
            id=job_id,
            session_id="sess_jobs",
            user_quota_id=quota_id,
            status=JobStatus[status],
            original_filename="a.png",
            original_size=len(data) if data else 10,
            quality_setting=50,
            created_at=datetime.utcnow(),
            expiry_date=datetime.utcnow() + timedelta(days=7),
            source_object_key=f"uploads/{job_id}",
            declared_size_bytes=len(data) if data else 10,
            declared_content_type="image/png",
            upload_expires_at=datetime.utcnow() + timedelta(minutes=30),
            credit_pool=pool,
            **cols,
        ))
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


def _uploaded_job(fake_store, status="PENDING", **cols):
    data = png_bytes()
    job_id = _add_job(status=status, data=data, **cols)
    fake_store.put(f"uploads/{job_id}", data, "image/png")
    return job_id


def _deliver(client, job_id, token=TOKEN):
    return client.post(PROCESS, json={"job_id": job_id}, headers={"X-Internal-Token": token})


# ---- enqueuer (no network: the library's request types and a fake client) ----

class _FakeTasksClient:
    def __init__(self, error=None):
        self.requests = []
        self.error = error

    def create_task(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return request.task


def _cloud_tasks(client):
    from app.services.job_enqueuer import CloudTasksEnqueuer

    return CloudTasksEnqueuer(client, QUEUE, "https://worker.invalid/", "tasks@p.iam.gserviceaccount.com", "https://worker.invalid")


def test_cloud_tasks_request_construction():
    from google.cloud import tasks_v2

    fake = _FakeTasksClient()
    enq = _cloud_tasks(fake)
    assert enq.enqueue("job-1") is True
    assert enq.enqueue("job-1", attempt=2) is True

    first, second = fake.requests
    assert isinstance(first, tasks_v2.CreateTaskRequest)
    assert first.parent == QUEUE
    assert first.task.name == f"{QUEUE}/tasks/job-1"
    assert second.task.name == f"{QUEUE}/tasks/job-1-r2"
    http = first.task.http_request
    assert http.url == "https://worker.invalid/internal/jobs/process"
    assert http.http_method == tasks_v2.HttpMethod.POST
    assert json.loads(http.body) == {"job_id": "job-1"}
    assert http.headers["Content-Type"] == "application/json"
    assert http.oidc_token.service_account_email == "tasks@p.iam.gserviceaccount.com"
    assert http.oidc_token.audience == "https://worker.invalid"
    assert first.task.dispatch_deadline.total_seconds() == 1800  # Cloud Tasks maximum for HTTP tasks


def test_cloud_tasks_already_exists_is_success_and_other_errors_fail():
    from google.api_core import exceptions

    assert _cloud_tasks(_FakeTasksClient(exceptions.AlreadyExists("dup"))).enqueue("job-1") is True
    assert _cloud_tasks(_FakeTasksClient(exceptions.ServiceUnavailable("down"))).enqueue("job-1") is False


def test_get_enqueuer_fails_closed(client, monkeypatch):
    from app.services import job_enqueuer

    s = job_enqueuer.settings
    monkeypatch.setattr(s, "job_enqueue_mode", None)
    assert job_enqueuer.get_enqueuer().enqueue("job-1") is False
    monkeypatch.setattr(s, "job_enqueue_mode", "unknown-mode")
    assert job_enqueuer.get_enqueuer().enqueue("job-1") is False
    monkeypatch.setattr(s, "job_enqueue_mode", "cloudtasks")  # queue/target/SA/audience unset
    assert isinstance(job_enqueuer.get_enqueuer(), job_enqueuer.DisabledEnqueuer)
    monkeypatch.setattr(s, "job_enqueue_mode", "direct")
    monkeypatch.setattr(s, "job_tasks_target_url", None)
    assert isinstance(job_enqueuer.get_enqueuer(), job_enqueuer.DisabledEnqueuer)

    fake = _FakeTasksClient()
    monkeypatch.setattr(job_enqueuer, "_cloud_tasks_client", lambda: fake)
    for key, value in {"job_enqueue_mode": "cloudtasks", "job_tasks_queue": QUEUE, "job_tasks_target_url": "https://w.invalid",
                       "job_tasks_sa_email": "sa@p.iam.gserviceaccount.com", "job_tasks_audience": "https://w.invalid"}.items():
        monkeypatch.setattr(s, key, value)
    assert job_enqueuer.get_enqueuer().enqueue("job-1") is True
    assert fake.requests[0].task.name == f"{QUEUE}/tasks/job-1"


def test_direct_enqueuer_posts_with_internal_token(monkeypatch):
    import httpx
    from app.services.job_enqueuer import DirectEnqueuer

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(calls[-1][1]["json"]["job_id"] == "ok" and 200 or 503)

    monkeypatch.setattr(httpx, "post", fake_post)
    enq = DirectEnqueuer("http://localhost:8000/", "secret")
    assert enq.enqueue("ok") is True
    assert enq.enqueue("bad") is False
    url, kwargs = calls[0]
    assert url == "http://localhost:8000/internal/jobs/process"
    assert kwargs["json"] == {"job_id": "ok"} and kwargs["headers"] == {"X-Internal-Token": "secret"}


# ---- POST /internal/jobs/process ----

def test_process_endpoint_requires_internal_auth(client, fake_store, monkeypatch):
    from app.utils import internal_auth

    job_id = _uploaded_job(fake_store)
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", None)  # fail closed
    monkeypatch.setattr(internal_auth.settings, "internal_auth_shared_secret", TOKEN)
    assert _deliver(client, job_id).status_code == 401
    monkeypatch.setattr(internal_auth.settings, "internal_auth_mode", "shared_secret")
    assert client.post(PROCESS, json={"job_id": job_id}).status_code == 401
    assert _deliver(client, job_id, token="wrong").status_code == 401
    assert _job(job_id).status.name == "PENDING"


def test_process_endpoint_done_then_duplicate_delivery_is_a_noop(client, fake_store, internal, output):
    job_id = _uploaded_job(fake_store)

    res = _deliver(client, job_id)
    assert (res.status_code, res.json()) == (200, {"status": "done"})
    done = _job(job_id)
    assert done.status.name == "DONE" and output.uploaded == [job_id]

    again = _deliver(client, job_id)
    assert (again.status_code, again.json()) == (200, {"status": "skipped"})
    after = _job(job_id)
    assert after.status.name == "DONE" and output.uploaded == [job_id]
    assert (after.r2_url, after.processing_finished_at, after.processing_error) == (done.r2_url, done.processing_finished_at, None)


def test_process_endpoint_terminal_outcomes_are_2xx(client, fake_store, internal, output):
    bad = _add_job(data=b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    fake_store.put(f"uploads/{bad}", b"\x89PNG\r\n\x1a\n" + b"\0" * 32, "image/png")
    missing = _add_job()  # no object in the bucket
    rejected = _add_job(status="REJECTED")

    assert _deliver(client, bad).json() == {"status": "failed"}
    assert _deliver(client, missing).json() == {"status": "failed"}
    assert "Source object not found" in _job(missing).processing_error
    assert _deliver(client, rejected).json() == {"status": "skipped"}
    assert _deliver(client, str(uuid.uuid4())).json() == {"status": "unknown"}
    for body in (b"not json", b"[1]", b'{"job_id": 5}', b"{}"):
        res = client.post(PROCESS, content=body, headers={"X-Internal-Token": TOKEN, "Content-Type": "application/json"})
        assert (res.status_code, res.json()) == (200, {"status": "ignored"})
    assert (_job(bad).status.name, _job(rejected).status.name) == ("FAILED", "REJECTED")


def test_process_endpoint_transient_storage_failure_is_503_and_releases_the_claim(client, fake_store, internal, monkeypatch):
    from app.workers import job_worker

    monkeypatch.setattr(job_worker, "r2_storage", _Output(ok=False))
    job_id = _uploaded_job(fake_store)

    res = _deliver(client, job_id)
    assert res.status_code == 503
    assert res.json() == {"status": "retry"} and "connection reset" not in res.text
    job = _job(job_id)
    assert job.status.name == "PENDING" and job.processing_error is None
    assert fake_store.deleted == []  # kept for the retry

    out = _Output()
    monkeypatch.setattr(job_worker, "r2_storage", out)
    assert _deliver(client, job_id).json() == {"status": "done"}  # the retry can claim it


def test_process_endpoint_source_download_error_is_retriable(client, fake_store, internal, output, monkeypatch):
    job_id = _uploaded_job(fake_store)

    def broken(key, chunk_size=1024 * 1024):
        raise ConnectionError("r2 timeout")
        yield b""

    monkeypatch.setattr(fake_store, "get_stream", broken)
    res = _deliver(client, job_id)
    assert res.status_code == 503 and "r2 timeout" not in res.text
    assert _job(job_id).status.name == "PENDING"


def test_process_endpoint_database_error_is_503_with_generic_body(client, fake_store, internal, monkeypatch):
    from app.workers import job_worker

    def boom(self, job_id):
        raise RuntimeError("could not connect to server: secret-host")

    monkeypatch.setattr(job_worker.JobWorker, "run", boom)
    res = _deliver(client, str(uuid.uuid4()))
    assert res.status_code == 503 and "secret-host" not in res.text


def test_lease_blocks_redelivery_until_it_expires(client, fake_store, internal, output):
    fresh = _uploaded_job(fake_store, status="PROCESSING", processing_started_at=datetime.utcnow() - timedelta(seconds=60))
    expired = _uploaded_job(fake_store, status="PROCESSING", processing_started_at=datetime.utcnow() - timedelta(seconds=1801))

    assert _deliver(client, fresh).json() == {"status": "skipped"}
    assert _job(fresh).status.name == "PROCESSING" and output.uploaded == []

    assert _deliver(client, expired).json() == {"status": "done"}
    assert _job(expired).status.name == "DONE" and output.uploaded == [expired]


def test_finish_without_the_current_claim_never_overwrites(client, fake_store, output):
    from app.models import db, JobStatus
    from app.workers.job_worker import JobWorker

    job_id = _uploaded_job(fake_store)
    s = db.get_session()
    try:
        assert JobWorker(s, store=fake_store).process_job(job_id) is True
        stale_claim = datetime.utcnow() - timedelta(hours=1)
        assert JobWorker(s, store=fake_store)._finish(job_id, stale_claim, status=JobStatus.FAILED, processing_error="late") is False
    finally:
        s.close()
    assert _job(job_id).status.name == "DONE" and _job(job_id).processing_error is None


# ---- /uploads/complete through the enqueuer ----

def test_complete_enqueues_first_attempt_and_records_enqueued_at(client, fake_store, monkeypatch):
    from app.services import job_enqueuer

    fake = FakeEnqueuer()
    monkeypatch.setattr(job_enqueuer, "get_enqueuer", lambda: fake)
    sid = "sess_complete_enqueuer"
    data = png_bytes()
    res = client.post("/api/v1/uploads/presign", json={"files": [{"name": "a.png", "size": len(data), "content_type": "image/png"}]},
                      headers={"X-Session-Id": sid})
    f = res.json()["files"][0]
    fake_store.put(f["object_key"], data, "image/png")
    before = datetime.utcnow()

    done = client.post("/api/v1/uploads/complete", json={"job_ids": [f["job_id"]]}, headers={"X-Session-Id": sid})
    assert done.status_code == 200, done.text
    assert fake.attempts == [(f["job_id"], 0)]
    job = _job(f["job_id"])
    assert job.status.name == "PENDING" and job.enqueued_at >= before and job.enqueue_attempts == 0


# ---- sweeper (U8) ----

def _seed_quota(credits_used=2):
    from app.models import db, UsersQuota

    quota_id = f"quota_{uuid.uuid4().hex[:8]}"
    s = db.get_session()
    try:
        s.add(UsersQuota(id=quota_id, session_id=f"sess_{quota_id}", credits_used=credits_used))
        s.commit()
    finally:
        s.close()
    return quota_id


def _credits(quota_id):
    from app.models import db, UsersQuota

    s = db.get_session()
    try:
        return s.get(UsersQuota, quota_id).credits_used
    finally:
        s.close()


def _sweep(enqueuer, store):
    from app.models import db
    from app.services.upload_sweeper import requeue_stale_jobs

    s = db.get_session()
    try:
        return requeue_stale_jobs(s, enqueuer, store)
    finally:
        s.close()


def _age(job_id, **cols):
    from app.models import db, Job

    s = db.get_session()
    try:
        s.query(Job).filter_by(id=job_id).update(cols)
        s.commit()
    finally:
        s.close()


def test_sweeper_reenqueues_stale_pending_with_new_task_names_then_fails(client, fake_store):
    from app.services.upload_sweeper import MAX_REENQUEUES

    quota_id = _seed_quota()
    old = datetime.utcnow() - timedelta(seconds=601)
    stale = _uploaded_job(fake_store, quota_id=quota_id, enqueued_at=old)
    fresh = _uploaded_job(fake_store, quota_id=quota_id, enqueued_at=datetime.utcnow())
    fake = FakeEnqueuer()

    _sweep(fake, fake_store)
    _sweep(fake, fake_store)  # immediately again: enqueued_at was just reset, so no second task
    assert [a for a in fake.attempts if a[0] in (stale, fresh)] == [(stale, 1)]
    assert _job(stale).status.name == "PENDING" and _job(stale).enqueue_attempts == 1

    for n in range(2, MAX_REENQUEUES + 1):
        _age(stale, enqueued_at=old)
        _sweep(fake, fake_store)
    assert [a for a in fake.attempts if a[0] == stale] == [(stale, n) for n in range(1, MAX_REENQUEUES + 1)]

    _age(stale, enqueued_at=old)
    _sweep(fake, fake_store)
    job = _job(stale)
    assert job.status.name == "FAILED" and job.processing_error == "Job could not be queued for processing"
    assert len([a for a in fake.attempts if a[0] == stale]) == MAX_REENQUEUES
    assert f"uploads/{stale}" in fake_store.deleted
    assert _job(fresh).status.name == "PENDING" and _job(fresh).enqueue_attempts == 0
    assert _credits(quota_id) == 2  # credit stays spent; never released twice


def test_sweeper_fails_stuck_processing_after_twice_the_lease(client, fake_store):
    quota_id = _seed_quota()
    stuck = _uploaded_job(fake_store, status="PROCESSING", quota_id=quota_id,
                          processing_started_at=datetime.utcnow() - timedelta(seconds=3601))
    leased = _uploaded_job(fake_store, status="PROCESSING", quota_id=quota_id,
                           processing_started_at=datetime.utcnow() - timedelta(seconds=1801))
    done = _uploaded_job(fake_store, status="DONE", quota_id=quota_id, processing_started_at=datetime.utcnow() - timedelta(days=1))
    fake = FakeEnqueuer()

    _sweep(fake, fake_store)
    _sweep(fake, fake_store)
    assert (_job(stuck).status.name, _job(stuck).processing_error) == ("FAILED", "Processing did not finish")
    assert (_job(leased).status.name, _job(done).status.name) == ("PROCESSING", "DONE")
    assert fake_store.deleted.count(f"uploads/{stuck}") == 1
    assert _credits(quota_id) == 2


def test_sweep_cli_exits_nonzero_when_reenqueue_fails(client, fake_store, capsys):
    from app.cli import sweep_uploads

    stale = _uploaded_job(fake_store, enqueued_at=datetime.utcnow() - timedelta(seconds=601))
    assert sweep_uploads.main() == 1  # JOB_ENQUEUE_MODE unset in tests: fail closed
    assert "enqueue failures: " in capsys.readouterr().out
    assert _job(stale).status.name == "PENDING"


# ---- expired-output cleanup CLI ----

class _Outputs:
    def __init__(self, ok=True):
        self.ok = ok
        self.deleted = []

    def delete_optimized_image(self, key):
        self.deleted.append(key)
        return self.ok


def test_cleanup_cli_is_idempotent(client, monkeypatch, capsys):
    from app.cli import cleanup_expired

    past = datetime.utcnow() - timedelta(days=1)
    expired = _add_job(status="DONE", r2_object_key=f"optimized/{uuid.uuid4()}.avif", r2_url="https://cdn.invalid/x")
    live = _add_job(status="DONE", r2_object_key=f"optimized/{uuid.uuid4()}.avif", r2_url="https://cdn.invalid/y")
    _age(expired, expiry_date=past)
    outputs = _Outputs()
    monkeypatch.setattr(cleanup_expired, "r2_storage", outputs)

    assert cleanup_expired.main() == 0
    assert cleanup_expired.main() == 0
    assert outputs.deleted.count(_job(expired).r2_object_key) == 1
    assert _job(expired).deleted_at is not None and _job(expired).r2_url is None
    assert _job(live).deleted_at is None and _job(live).r2_url == "https://cdn.invalid/y"
    assert "expired outputs deleted:" in capsys.readouterr().out


def test_cleanup_cli_exits_nonzero_when_r2_delete_fails(client, monkeypatch):
    from app.cli import cleanup_expired

    job_id = _add_job(status="DONE", r2_object_key=f"optimized/{uuid.uuid4()}.avif", r2_url="https://cdn.invalid/z")
    _age(job_id, expiry_date=datetime.utcnow() - timedelta(days=1))
    monkeypatch.setattr(cleanup_expired, "r2_storage", _Outputs(ok=False))
    assert cleanup_expired.main() == 1
    assert _job(job_id).deleted_at is None  # retried next run
    monkeypatch.setattr(cleanup_expired, "r2_storage", _Outputs())
    assert cleanup_expired.main() == 0


def test_no_transaction_is_held_while_the_image_is_processed(client, fake_store, output, monkeypatch):
    # Review finding (T-007): the worker shares the API's small pool; processing must not pin a connection.
    from app.models import db
    from app.workers.job_worker import JobWorker

    job_id = _uploaded_job(fake_store)
    seen = []
    real = JobWorker._optimize

    def spy(self, job):
        seen.append(self.db_session.in_transaction())
        return real(self, job)

    monkeypatch.setattr(JobWorker, "_optimize", spy)
    s = db.get_session()
    try:
        assert JobWorker(s, store=fake_store).process_job(job_id) is True
    finally:
        s.close()
    assert seen == [False]


def test_source_head_error_is_retriable(client, fake_store, internal, output, monkeypatch):
    job_id = _uploaded_job(fake_store)

    def broken_head(key):
        raise ConnectionError("r2 head timeout")

    monkeypatch.setattr(fake_store, "head", broken_head)
    res = _deliver(client, job_id)
    assert res.status_code == 503 and "head timeout" not in res.text
    job = _job(job_id)
    assert job.status.name == "PENDING" and job.enqueued_at is not None  # release restarts the stale-PENDING clock
