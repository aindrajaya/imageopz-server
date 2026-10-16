"""
Expired-output cleanup: DONE jobs past expiry_date lose their R2 object and URL and get deleted_at.
Same logic as the former in-process scheduled job; entry point `python -m app.cli.cleanup_expired` (scheduled by T-008).
Idempotent: a cleaned job has deleted_at set and is not selected again; a failed R2 delete is retried next run.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models import Job, JobStatus

logger = logging.getLogger("imageopz.cleanup")


def cleanup_expired_outputs(db: Session, storage, now: Optional[datetime] = None) -> Tuple[int, int]:
    """Returns (deleted, failed). storage is the outputs R2 client (image_service.r2_storage) or None."""
    now = now or datetime.utcnow()
    expired_jobs = db.query(Job).filter(
        Job.expiry_date <= now,
        Job.deleted_at == None,  # noqa: E711
        Job.status == JobStatus.DONE,
    ).all()

    deleted_count = 0
    failed_count = 0
    for job in expired_jobs:
        try:
            if job.r2_object_key and storage:
                if not storage.delete_optimized_image(job.r2_object_key):
                    logger.warning("Failed to delete %s from R2", job.r2_object_key)
                    failed_count += 1
                    continue
            job.deleted_at = now
            job.r2_url = None
            deleted_count += 1
        except Exception:
            logger.exception("Error cleaning up job %s", job.id)
            failed_count += 1

    db.commit()
    logger.info("Cleanup completed: %s deleted, %s failed", deleted_count, failed_count)
    return deleted_count, failed_count
