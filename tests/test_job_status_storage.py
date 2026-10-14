"""
Pins the raw stored representation of jobs.status, independent of the ORM.
Current code stores the enum member NAMES (e.g. 'PENDING'), not the lowercase values.
Schema changes (Alembic baseline, enum handling) must not change what is written.
"""
import uuid

import pytest
from sqlalchemy import text


def _raw_status(job_id):
    from app.models import db

    with db.engine.connect() as conn:
        return conn.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}).scalar_one()


def test_new_job_default_status_is_stored_as_PENDING(client):
    from app.models import db, Job

    job_id = str(uuid.uuid4())
    s = db.get_session()
    try:
        s.add(Job(id=job_id, session_id="sess_status_default"))
        s.commit()
    finally:
        s.close()
    assert _raw_status(job_id) == "PENDING"


@pytest.mark.parametrize("name", ["PENDING", "PROCESSING", "DONE", "FAILED"])
def test_job_status_is_stored_as_member_name(client, name):
    from app.models import db, Job, JobStatus

    job_id = str(uuid.uuid4())
    s = db.get_session()
    try:
        s.add(Job(id=job_id, session_id="sess_status_storage", status=JobStatus[name]))
        s.commit()
    finally:
        s.close()
    assert _raw_status(job_id) == name
