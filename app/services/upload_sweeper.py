"""
Upload and job sweeper, entry point `python -m app.cli.sweep_uploads` (scheduled by T-008):
- D8: UPLOADING jobs past upload_expires_at become REJECTED "expired" and release their credit once.
- U8 (ADR-005): PENDING jobs queued longer than JOB_STALE_PENDING_SECONDS ago are re-enqueued under a new task name
  ("-r<n>"), at most MAX_REENQUEUES times, then FAILED; PROCESSING jobs older than twice JOB_LEASE_SECONDS are
  FAILED. A FAILED job keeps its credit spent, as for every other worker failure.
Every transition is a conditional UPDATE, so runs are idempotent and safe next to /uploads/complete and the worker.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from app.config import settings
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


MAX_REENQUEUES = 3
NOT_QUEUED_ERROR = "Job could not be queued for processing"
STUCK_ERROR = "Processing did not finish"


def _fail(db: Session, job: Job, error: str, now: datetime, **where) -> bool:
    res = db.execute(
        update(Job)
        .where(Job.id == job.id, *[getattr(Job, k) == v for k, v in where.items()])
        .values(status=JobStatus.FAILED, processing_error=error, processing_finished_at=now)
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1


def requeue_stale_jobs(db: Session, enqueuer, store: Optional[UploadStore], now: Optional[datetime] = None) -> Tuple[int, int, int]:
    """Returns (re-enqueued, enqueue failures, failed). Commits each claim before enqueueing it."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(seconds=settings.job_stale_pending_seconds)
    stale = (
        db.query(Job)
        .filter(
            Job.status == JobStatus.PENDING,
            or_(Job.enqueued_at < cutoff, and_(Job.enqueued_at == None, Job.created_at < cutoff)),  # noqa: E711
        )
        .order_by(Job.id)
        .all()
    )
    stuck = (
        db.query(Job)
        .filter(
            Job.status == JobStatus.PROCESSING,
            Job.processing_started_at < now - timedelta(seconds=2 * settings.job_lease_seconds),
        )
        .order_by(Job.id)
        .all()
    )
    requeue, failed = [], []
    for job in stale:
        attempts = job.enqueue_attempts or 0
        if attempts >= MAX_REENQUEUES:
            if _fail(db, job, NOT_QUEUED_ERROR, now, status=JobStatus.PENDING, enqueue_attempts=attempts):
                failed.append(job)
            continue
        # The attempt counter is the claim: two concurrent sweepers cannot both bump it, so each "-r<n>" is used once.
        res = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == JobStatus.PENDING, Job.enqueue_attempts == attempts)
            .values(enqueue_attempts=attempts + 1, enqueued_at=now)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount == 1:
            requeue.append((job.id, attempts + 1))
    for job in stuck:
        if _fail(db, job, STUCK_ERROR, now, status=JobStatus.PROCESSING, processing_started_at=job.processing_started_at):
            failed.append(job)
    db.commit()

    enqueue_failures = sum(1 for job_id, attempt in requeue if not enqueuer.enqueue(job_id, attempt))
    if store is not None:
        for job in failed:
            delete_source_best_effort(store, job.source_object_key)
    logger.info(
        "Job sweeper: %d re-enqueued (%d enqueue failures), %d failed",
        len(requeue), enqueue_failures, len(failed),
    )
    return len(requeue), enqueue_failures, len(failed)
