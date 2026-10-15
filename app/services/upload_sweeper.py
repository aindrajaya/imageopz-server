"""
Expired-upload sweeper (D8): UPLOADING jobs past upload_expires_at become REJECTED "expired" and release their
credit once. Idempotent; safe to run concurrently with /uploads/complete (conditional transitions).
Scheduling is T-007/T-008; entry point: `python -m app.cli.sweep_uploads`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Job, JobStatus
from app.services.upload_store import UploadStore
from app.services.uploads import EXPIRED, delete_source_best_effort, reject_upload

logger = logging.getLogger("imageopz.uploads.sweeper")


def expire_stale_uploads(db: Session, store: Optional[UploadStore], now: Optional[datetime] = None) -> int:
    """Reject expired uploads; returns how many jobs this call moved to REJECTED."""
    now = now or datetime.utcnow()
    stale = (
        db.query(Job)
        .filter(Job.status == JobStatus.UPLOADING, Job.upload_expires_at != None, Job.upload_expires_at < now)  # noqa: E711
        .order_by(Job.id)  # same lock order as /uploads/complete (no deadlock between concurrent rejectors)
        .all()
    )
    expired = [job for job in stale if reject_upload(db, job, EXPIRED, now)]
    db.commit()
    if store is not None:
        for job in expired:
            delete_source_best_effort(store, job.source_object_key)
    logger.info("Upload sweeper: %d expired upload(s) rejected", len(expired))
    return len(expired)
