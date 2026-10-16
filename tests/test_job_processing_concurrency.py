"""
Two simultaneous deliveries of the same job: exactly one claims and processes it (ADR-005). Postgres only
(TEST_DATABASE_URL): SQLite serializes writers on a database lock and cannot show row-lock behavior.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from tests.test_job_processing import _job, _uploaded_job

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="requires TEST_DATABASE_URL (Postgres)")


def test_second_claim_waits_for_the_first_and_then_loses(client, fake_store):
    # Deterministic interleaving: B's UPDATE blocks on A's row lock and re-checks status after A commits.
    from sqlalchemy.orm import Session
    from app.models import db
    from app.workers.job_worker import claim_job

    job_id = _uploaded_job(fake_store)
    a, b = Session(db.engine), Session(db.engine)
    try:
        assert claim_job(a, job_id, datetime.utcnow(), 1800) is True  # uncommitted, holds the row lock
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("b", claim_job(b, job_id, datetime.utcnow(), 1800)))
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
    assert _job(job_id).status.name == "PROCESSING"


@pytest.mark.parametrize("start", ["PENDING", "EXPIRED_LEASE"])
def test_concurrent_deliveries_process_exactly_once(client, fake_store, monkeypatch, start):
    from app.models import db
    from app.workers import job_worker
    from tests.test_job_processing import _Output

    output = _Output()
    monkeypatch.setattr(job_worker, "r2_storage", output)
    if start == "PENDING":
        job_id = _uploaded_job(fake_store)
    else:
        job_id = _uploaded_job(fake_store, status="PROCESSING", processing_started_at=datetime.utcnow() - timedelta(seconds=1801))
    barrier = threading.Barrier(2)

    def deliver(_):
        s = db.get_session()
        try:
            barrier.wait()
            return job_worker.JobWorker(s, store=fake_store).run(job_id)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(o.value for o in pool.map(deliver, range(2)))

    assert outcomes == ["done", "skipped"]
    assert output.uploaded == [job_id]
    assert _job(job_id).status.name == "DONE"
