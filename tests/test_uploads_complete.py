"""POST /api/v1/uploads/complete: verification, idempotency, exactly-once credit release, commit-before-enqueue."""
from datetime import datetime, timedelta

import pytest

from tests.upload_fakes import png_bytes

PNG = "image/png"


@pytest.fixture()
def enqueued(monkeypatch):
    from app.workers.job_worker import job_queue

    calls = []
    monkeypatch.setattr(job_queue, "enqueue_job", lambda job_id: calls.append(job_id) or True)
    return calls


def _presign(client, sid, files):
    res = client.post(
        "/api/v1/uploads/presign",
        json={"files": [{"name": n, "size": s, "content_type": t} for n, s, t in files]},
        headers={"X-Session-Id": sid},
    )
    assert res.status_code == 201, res.text
    return res.json()["files"]


def _complete(client, sid, job_ids):
    return client.post("/api/v1/uploads/complete", json={"job_ids": job_ids}, headers={"X-Session-Id": sid})


def _credits(sid):
    from app.models import db, UsersQuota

    s = db.get_session()
    try:
        return s.query(UsersQuota).filter_by(session_id=sid).first().credits_used
    finally:
        s.close()


def _job(job_id):
    from app.models import db, Job

    s = db.get_session()
    try:
        return s.get(Job, job_id)
    finally:
        s.close()


def _upload_png(client, fake_store, sid, name="a.png"):
    data = png_bytes()
    f = _presign(client, sid, [(name, len(data), PNG)])[0]
    fake_store.put(f["object_key"], data, PNG)
    return f


def test_complete_accepts_valid_upload_and_enqueues_once(client, fake_store, enqueued):
    f = _upload_png(client, fake_store, "sess_complete_ok")
    res = _complete(client, "sess_complete_ok", [f["job_id"]])
    assert res.status_code == 200, res.text
    assert res.json() == {"jobs": [{"job_id": f["job_id"], "status": "pending", "reason": None}]}
    assert enqueued == [f["job_id"]]
    assert _job(f["job_id"]).status.name == "PENDING"
    assert _credits("sess_complete_ok") == 1  # the credit stays spent
    assert f["object_key"] in fake_store.objects  # the worker deletes it after processing

    again = _complete(client, "sess_complete_ok", [f["job_id"], f["job_id"]])
    assert again.status_code == 200, again.text
    assert again.json()["jobs"] == [{"job_id": f["job_id"], "status": "pending", "reason": None}]
    assert enqueued == [f["job_id"]]  # no double enqueue


def _corrupt(fake_store, f, case):
    data = png_bytes()
    if case == "missing":
        return
    if case == "size_mismatch":
        fake_store.put(f["object_key"], data + b"extra", PNG)
    elif case == "type_mismatch":
        fake_store.put(f["object_key"], data, "image/jpeg")
    elif case == "invalid_image":
        fake_store.put(f["object_key"], b"GIF89a" + b"\0" * (len(data) - 6), PNG)
    elif case == "invalid_image_header":
        fake_store.put(f["object_key"], data[:8] + b"\0" * (len(data) - 8), PNG)
    elif case == "too_many_pixels":
        fake_store.put(f["object_key"], data, PNG)


@pytest.mark.parametrize(
    "case, reason",
    [
        ("missing", "missing"),
        ("size_mismatch", "size_mismatch"),
        ("type_mismatch", "type_mismatch"),
        ("invalid_image", "invalid_image"),
        ("invalid_image_header", "invalid_image"),
        ("too_many_pixels", "too_many_pixels"),
        ("expired", "expired"),
    ],
)
def test_complete_rejections_release_the_credit_and_delete_the_object(client, fake_store, enqueued, monkeypatch, case, reason):
    from app.services import uploads

    sid = f"sess_reject_{case}"
    data = png_bytes()
    f = _presign(client, sid, [("a.png", len(data), PNG)])[0]
    assert _credits(sid) == 1
    _corrupt(fake_store, f, case)
    if case == "too_many_pixels":
        monkeypatch.setattr(uploads.settings, "max_image_pixels", 63)  # the 8x8 PNG has 64
    if case == "expired":
        fake_store.put(f["object_key"], data, PNG)
        from app.models import db, Job

        s = db.get_session()
        try:
            s.get(Job, f["job_id"]).upload_expires_at = datetime.utcnow() - timedelta(seconds=1)
            s.commit()
        finally:
            s.close()

    res = _complete(client, sid, [f["job_id"]])
    assert res.status_code == 200, res.text
    assert res.json()["jobs"] == [{"job_id": f["job_id"], "status": "rejected", "reason": reason}]
    job = _job(f["job_id"])
    assert (job.status.name, job.rejection_reason) == ("REJECTED", reason)
    assert _credits(sid) == 0
    assert enqueued == []
    assert f["object_key"] not in fake_store.objects

    status = client.get(f"/api/v1/status/{f['job_id']}")
    assert status.status_code == 200, status.text
    assert (status.json()["status"], status.json()["rejection_reason"]) == ("rejected", reason)


def test_complete_requires_ownership(client, fake_store, enqueued):
    f = _upload_png(client, fake_store, "sess_owner")
    _presign(client, "sess_other", [("x.png", 10, PNG)])  # the other session has its own quota row
    res = _complete(client, "sess_other", [f["job_id"]])
    assert res.status_code == 404, res.text
    assert res.json()["error"] == "JOB_NOT_FOUND"
    assert _complete(client, "sess_owner", ["no-such-job"]).status_code == 404
    assert _job(f["job_id"]).status.name == "UPLOADING"
    assert enqueued == []


def test_complete_rejects_more_job_ids_than_the_tier_allows(client, fake_store, enqueued):
    files = _presign(client, "sess_too_many", [(f"{i}.png", 10, PNG) for i in range(3)])
    res = _complete(client, "sess_too_many", [f["job_id"] for f in files] + ["extra"])
    assert res.status_code == 400, res.text
    assert res.json()["error"] == "TOO_MANY_JOBS"


def test_credit_released_exactly_once_across_reject_sweeper_and_repeat(client, fake_store, enqueued):
    from app.models import db
    from app.services.upload_sweeper import expire_stale_uploads

    sid = "sess_release_once"
    f = _presign(client, sid, [("a.png", 100, PNG)])[0]  # never uploaded
    keep = _upload_png(client, fake_store, sid, "b.png")
    assert _credits(sid) == 2

    assert _complete(client, sid, [f["job_id"]]).json()["jobs"][0]["reason"] == "missing"
    assert _credits(sid) == 1

    s = db.get_session()
    try:
        assert expire_stale_uploads(s, fake_store, now=datetime.utcnow() + timedelta(hours=1)) >= 1  # other tests' jobs too
    finally:
        s.close()
    assert _credits(sid) == 0  # only `keep` was released by the sweeper; `f` was not released twice
    for _ in range(2):
        res = _complete(client, sid, [f["job_id"], keep["job_id"]])
        assert [j["reason"] for j in res.json()["jobs"]] == ["missing", "expired"]
    assert _credits(sid) == 0
    assert enqueued == []


def test_partial_enqueue_failure_reverts_only_failed_jobs(client, fake_store, monkeypatch):
    from app.workers.job_worker import job_queue

    sid = "sess_partial_enqueue"
    data = png_bytes()
    files = _presign(client, sid, [("a.png", len(data), PNG), ("b.png", len(data), PNG)])
    for f in files:
        fake_store.put(f["object_key"], data, PNG)
    first, second = files[0]["job_id"], files[1]["job_id"]

    calls = []
    monkeypatch.setattr(job_queue, "enqueue_job", lambda job_id: calls.append(job_id) or job_id == first)
    res = _complete(client, sid, [first, second])
    assert res.status_code == 503, res.text
    assert res.json()["error"] == "QUEUE_UNAVAILABLE"
    assert res.json()["details"]["jobs"] == [
        {"job_id": first, "status": "pending", "reason": None},
        {"job_id": second, "status": "uploading", "reason": None},
    ]
    assert (_job(first).status.name, _job(second).status.name) == ("PENDING", "UPLOADING")
    assert _credits(sid) == 2

    monkeypatch.setattr(job_queue, "enqueue_job", lambda job_id: calls.append(job_id) or True)
    retry = _complete(client, sid, [first, second])
    assert retry.status_code == 200, retry.text
    assert [j["status"] for j in retry.json()["jobs"]] == ["pending", "pending"]
    assert calls == [first, second, second]  # the already-queued job is not enqueued again


def test_complete_returns_503_when_queue_unavailable(client, fake_store):
    # Replaces test_optimize_returns_503_when_queue_unavailable: Redis is unreachable in tests (conftest).
    f = _upload_png(client, fake_store, "sess_queue_down")
    res = _complete(client, "sess_queue_down", [f["job_id"]])
    assert res.status_code == 503, res.text
    assert res.json()["error"] == "QUEUE_UNAVAILABLE"
    assert _job(f["job_id"]).status.name == "UPLOADING"
    assert _credits("sess_queue_down") == 1  # still reserved; a retry or the sweeper settles it
