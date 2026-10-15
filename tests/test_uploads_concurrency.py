"""
Credit reservation is one conditional UPDATE, so concurrent presigns cannot overspend. Postgres only
(TEST_DATABASE_URL): SQLite serializes writers on a database lock and cannot show row-lock behavior.
"""
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires TEST_DATABASE_URL (Postgres)")


def _seed_quota(credits_used=0):
    from app.models import db, UsersQuota

    quota_id, sid = f"quota_{uuid.uuid4().hex[:8]}", f"sess_{uuid.uuid4().hex[:8]}"
    s = db.get_session()
    try:
        s.add(UsersQuota(id=quota_id, session_id=sid, credits_used=credits_used))
        s.commit()
    finally:
        s.close()
    return quota_id, sid


def _credits(quota_id):
    from app.models import db, UsersQuota

    s = db.get_session()
    try:
        return s.get(UsersQuota, quota_id).credits_used
    finally:
        s.close()


def test_second_reservation_waits_for_the_first_and_then_fails(client):
    # Deterministic interleaving: B's UPDATE blocks on A's row lock and re-checks the limit after A commits.
    from sqlalchemy.orm import Session
    from app.models import db
    from app.services.uploads import reserve_credits

    quota_id, _ = _seed_quota()  # free limit is 3
    a, b = Session(db.engine), Session(db.engine)
    try:
        assert reserve_credits(a, quota_id, "free", 2) is True  # uncommitted, holds the row lock
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("b", reserve_credits(b, quota_id, "free", 2)))
        t.start()
        time.sleep(0.5)
        assert t.is_alive(), "B should be blocked on A's row lock"
        a.commit()
        t.join(timeout=10)
        assert result["b"] is False
        b.rollback()
    finally:
        a.close()
        b.close()
    assert _credits(quota_id) == 2


def test_concurrent_presigns_exceeding_the_limit_exactly_one_succeeds(client, fake_store):
    from fastapi.testclient import TestClient
    from app.models import db, Job
    import app.main as main

    quota_id, sid = _seed_quota()
    barrier = threading.Barrier(2)
    body = {"files": [{"name": f"{i}.png", "size": 10, "content_type": "image/png"} for i in range(2)]}

    def presign(_):
        with TestClient(main.app) as c:
            barrier.wait()
            return c.post("/api/v1/uploads/presign", json=body, headers={"X-Session-Id": sid}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(presign, range(2)))

    assert codes == [201, 429]
    assert _credits(quota_id) == 2
    s = db.get_session()
    try:
        assert s.query(Job).filter_by(user_quota_id=quota_id).count() == 2
    finally:
        s.close()


def _uploading_job(quota_id, sid, expired=True):
    from datetime import datetime, timedelta
    from app.models import db, Job, JobStatus

    job_id = str(uuid.uuid4())
    s = db.get_session()
    try:
        s.add(Job(
            id=job_id, session_id=sid, user_quota_id=quota_id, status=JobStatus.UPLOADING, original_filename="a.png",
            original_size=10, created_at=datetime.utcnow(), expiry_date=datetime.utcnow() + timedelta(days=7),
            source_object_key=f"uploads/{job_id}", declared_size_bytes=10, declared_content_type="image/png",
            upload_expires_at=datetime.utcnow() + (timedelta(seconds=-1) if expired else timedelta(minutes=30)),
            credit_pool="free",
        ))
        s.commit()
    finally:
        s.close()
    return job_id


def test_rejection_racing_the_sweeper_releases_the_credit_once(client):
    # A /complete rejection holds the job row; the sweeper's conditional transition waits, then matches nothing.
    from datetime import datetime
    from sqlalchemy.orm import Session
    from app.models import db, Job
    from app.services.upload_sweeper import expire_stale_uploads
    from app.services.uploads import reject_upload

    quota_id, sid = _seed_quota(credits_used=1)
    job_id = _uploading_job(quota_id, sid)
    a, b = Session(db.engine), Session(db.engine)
    try:
        assert reject_upload(a, a.get(Job, job_id), "missing", datetime.utcnow()) is True  # uncommitted
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("swept", expire_stale_uploads(b, None)))
        t.start()
        time.sleep(0.5)
        assert t.is_alive(), "the sweeper should wait on the job row lock"
        a.commit()
        t.join(timeout=10)
    finally:
        a.close()
        b.close()
    s = db.get_session()
    try:
        assert (s.get(Job, job_id).rejection_reason, _credits(quota_id)) == ("missing", 0)
    finally:
        s.close()


def test_concurrent_complete_calls_release_the_credit_once(client, fake_store):
    from fastapi.testclient import TestClient
    from app.models import db, Job
    import app.main as main

    quota_id, sid = _seed_quota(credits_used=3)
    job_ids = [_uploading_job(quota_id, sid, expired=False) for _ in range(3)]  # never uploaded -> "missing"
    barrier = threading.Barrier(2)

    def complete(_):
        with TestClient(main.app) as c:
            barrier.wait()
            return c.post("/api/v1/uploads/complete", json={"job_ids": job_ids}, headers={"X-Session-Id": sid})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(complete, range(2)))

    assert [r.status_code for r in responses] == [200, 200]
    for r in responses:
        assert [(j["status"], j["reason"]) for j in r.json()["jobs"]] == [("rejected", "missing")] * 3
    assert _credits(quota_id) == 0  # three credits, each released exactly once
    s = db.get_session()
    try:
        assert {s.get(Job, j).status.name for j in job_ids} == {"REJECTED"}
    finally:
        s.close()
