"""Worker reads presigned uploads from the store and deletes them; the expired-upload sweeper is idempotent."""
import os
import uuid
from datetime import datetime, timedelta

from tests.upload_fakes import png_bytes


class _Queue:
    redis_client = None

    def set_job_progress(self, *args, **kwargs):
        pass


class _Output:
    def __init__(self):
        self.uploaded = []

    def upload_optimized_image(self, data, original_filename, job_id):
        self.uploaded.append(job_id)
        return True, f"optimized/{job_id}.avif", f"https://cdn.invalid/optimized/{job_id}.avif"


def _add_job(status="PENDING", sid="sess_worker", quota_id=None, expires_in=timedelta(minutes=30), data=None, pool="free"):
    from app.models import db, Job, JobStatus

    job_id = str(uuid.uuid4())
    s = db.get_session()
    try:
        s.add(Job(
            id=job_id,
            session_id=sid,
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
            upload_expires_at=datetime.utcnow() + expires_in,
            credit_pool=pool,
        ))
        s.commit()
    finally:
        s.close()
    return job_id


def _run_worker(job_id, store, monkeypatch, created_tmp):
    import tempfile
    from app.models import db
    from app.workers import job_worker

    output = _Output()
    monkeypatch.setattr(job_worker, "r2_storage", output)
    real_mkstemp = tempfile.mkstemp

    def recording_mkstemp(*a, **k):
        fd, path = real_mkstemp(*a, **k)
        created_tmp.append(path)
        return fd, path

    monkeypatch.setattr(job_worker.tempfile, "mkstemp", recording_mkstemp)
    s = db.get_session()
    try:
        ok = job_worker.JobWorker(s, queue=_Queue(), store=store).process_job(job_id)
    finally:
        s.close()
    return ok, output


def _job(job_id):
    from app.models import db, Job

    s = db.get_session()
    try:
        return s.get(Job, job_id)
    finally:
        s.close()


def test_worker_reads_source_from_store_and_deletes_it(client, fake_store, monkeypatch):
    data = png_bytes(16, 12)
    job_id = _add_job(data=data)
    fake_store.put(f"uploads/{job_id}", data, "image/png")
    tmp = []

    ok, output = _run_worker(job_id, fake_store, monkeypatch, tmp)

    assert ok is True
    job = _job(job_id)
    assert job.status.name == "DONE"
    assert (job.original_width, job.original_height) == (16, 12)
    assert output.uploaded == [job_id]
    assert fake_store.deleted == [f"uploads/{job_id}"]
    assert len(tmp) == 1 and not os.path.exists(tmp[0])


def test_worker_deletes_source_after_failure(client, fake_store, monkeypatch):
    data = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
    job_id = _add_job(data=data)
    fake_store.put(f"uploads/{job_id}", data, "image/png")
    tmp = []

    ok, output = _run_worker(job_id, fake_store, monkeypatch, tmp)

    assert ok is False
    assert _job(job_id).status.name == "FAILED"
    assert output.uploaded == []
    assert fake_store.deleted == [f"uploads/{job_id}"]
    assert not os.path.exists(tmp[0])


def test_worker_refuses_source_larger_than_declared(client, fake_store, monkeypatch):
    data = png_bytes()
    job_id = _add_job(data=data)
    fake_store.put(f"uploads/{job_id}", data + b"x" * 10, "image/png")  # swapped after /complete

    ok, _ = _run_worker(job_id, fake_store, monkeypatch, [])

    assert ok is False
    job = _job(job_id)
    assert job.status.name == "FAILED" and "larger than the declared size" in job.processing_error
    assert fake_store.deleted == [f"uploads/{job_id}"]


def test_worker_skips_jobs_still_uploading(client, fake_store, monkeypatch):
    job_id = _add_job(status="UPLOADING")
    ok, output = _run_worker(job_id, fake_store, monkeypatch, [])
    assert ok is False
    assert _job(job_id).status.name == "UPLOADING"
    assert output.uploaded == [] and fake_store.deleted == []


def test_sweeper_rejects_expired_uploads_once(client, fake_store):
    from app.models import db, UsersQuota
    from app.services.upload_sweeper import expire_stale_uploads

    s = db.get_session()
    try:
        s.add(UsersQuota(id="quota_sweeper", session_id="sess_sweeper", credits_used=2, pro_credits_used=1))
        s.commit()
    finally:
        s.close()
    stale_free = _add_job(status="UPLOADING", sid="sess_sweeper", quota_id="quota_sweeper", expires_in=timedelta(seconds=-1))
    stale_pro = _add_job(status="UPLOADING", sid="sess_sweeper", quota_id="quota_sweeper", expires_in=timedelta(seconds=-1), pool="pro")
    fresh = _add_job(status="UPLOADING", sid="sess_sweeper", quota_id="quota_sweeper")
    fake_store.put(f"uploads/{stale_free}", b"x", "image/png")

    s = db.get_session()
    try:
        assert expire_stale_uploads(s, fake_store) >= 2
        assert expire_stale_uploads(s, fake_store) == 0  # idempotent
    finally:
        s.close()

    assert [(_job(j).status.name, _job(j).rejection_reason) for j in (stale_free, stale_pro, fresh)] == [
        ("REJECTED", "expired"), ("REJECTED", "expired"), ("UPLOADING", None),
    ]
    s = db.get_session()
    try:
        q = s.get(UsersQuota, "quota_sweeper")
        assert (q.credits_used, q.pro_credits_used) == (1, 0)  # each pool released exactly once
    finally:
        s.close()
    assert f"uploads/{stale_free}" not in fake_store.objects
    assert fake_store.deleted.count(f"uploads/{stale_free}") == 1


def test_sweep_uploads_cli_runs(client, fake_store, capsys):
    from app.cli import sweep_uploads

    assert sweep_uploads.main() == 0
    assert "expired uploads rejected:" in capsys.readouterr().out


def _bmp_bytes():
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 255)).save(buf, format="BMP")
    return buf.getvalue()


def test_worker_rejects_source_swapped_to_another_format_after_complete(client, fake_store, monkeypatch):
    # The presigned URL stays valid after /complete: a same-length re-PUT with another Pillow-decodable format
    # must not reach that decoder (worker is the final guard, D3).
    bmp = _bmp_bytes()
    job_id = _add_job(data=bmp)  # declared image/png with the BMP's exact length, so size checks pass
    fake_store.put(f"uploads/{job_id}", bmp, "image/png")

    ok, output = _run_worker(job_id, fake_store, monkeypatch, [])

    assert ok is False
    job = _job(job_id)
    assert job.status.name == "FAILED" and "does not match the declared image type" in job.processing_error
    assert output.uploaded == []
    assert fake_store.deleted == [f"uploads/{job_id}"]


def test_worker_duplicate_delivery_keeps_the_finished_result(client, fake_store, monkeypatch):
    data = png_bytes()
    job_id = _add_job(data=data)
    fake_store.put(f"uploads/{job_id}", data, "image/png")
    assert _run_worker(job_id, fake_store, monkeypatch, [])[0] is True

    ok, output = _run_worker(job_id, fake_store, monkeypatch, [])  # second delivery of the same id

    assert ok is False
    job = _job(job_id)
    assert job.status.name == "DONE" and job.processing_error is None
    assert output.uploaded == []


def test_worker_skips_rejected_jobs(client, fake_store, monkeypatch):
    job_id = _add_job(status="REJECTED")
    ok, output = _run_worker(job_id, fake_store, monkeypatch, [])
    assert ok is False
    assert _job(job_id).status.name == "REJECTED"
    assert output.uploaded == [] and fake_store.deleted == []


def test_worker_fails_job_when_outputs_store_is_not_configured(client, fake_store, monkeypatch):
    # U14: without outputs storage the worker used to mark the job DONE with no uploaded result.
    from app.models import db, UsersQuota
    from app.workers import job_worker

    s = db.get_session()
    try:
        s.add(UsersQuota(id="quota_no_outputs", session_id="sess_no_outputs", credits_used=1))
        s.commit()
    finally:
        s.close()
    data = png_bytes()
    job_id = _add_job(data=data, sid="sess_no_outputs", quota_id="quota_no_outputs")
    fake_store.put(f"uploads/{job_id}", data, "image/png")
    monkeypatch.setattr(job_worker, "r2_storage", None)

    s = db.get_session()
    try:
        ok = job_worker.JobWorker(s, queue=_Queue(), store=fake_store).process_job(job_id)
    finally:
        s.close()

    assert ok is False
    job = _job(job_id)
    assert job.status.name == "FAILED" and "not configured" in job.processing_error
    assert job.r2_url is None and job.r2_object_key is None and job.optimized_size is None
    assert fake_store.deleted == [f"uploads/{job_id}"]
    s = db.get_session()
    try:
        assert s.get(UsersQuota, "quota_no_outputs").credits_used == 1  # no credit released
    finally:
        s.close()
