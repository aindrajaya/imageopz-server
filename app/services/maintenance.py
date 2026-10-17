"""
Scheduled maintenance shared by the CLIs (`python -m app.cli.sweep_uploads`, `python -m app.cli.cleanup_expired`) and the
worker endpoints `POST /internal/maintenance/{sweep,cleanup}` (Cloud Scheduler, ADR-005 amendment). Both are idempotent.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.services import job_enqueuer, upload_store
from app.services.output_cleanup import cleanup_expired_outputs
from app.services.upload_sweeper import expire_stale_uploads, requeue_stale_jobs

logger = logging.getLogger("imageopz.uploads.sweeper")


@dataclass
class SweepCounts:
    expired_uploads: int
    requeued: int
    enqueue_failures: int
    failed: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CleanupCounts:
    deleted: int
    failed: int

    def as_dict(self) -> dict:
        return asdict(self)


def sweep(session: Session) -> SweepCounts:
    """Expired uploads, stale PENDING re-enqueue, stuck PROCESSING (app/services/upload_sweeper.py). Raises on DB errors."""
    try:
        store = upload_store.get_upload_store()
    except upload_store.UploadStoreNotConfigured:
        # Credits are still released; objects are left to the bucket lifecycle rule.
        logger.warning("Uploads bucket not configured; objects will not be deleted")
        store = None
    expired = expire_stale_uploads(session, store)
    requeued, enqueue_failures, failed = requeue_stale_jobs(session, job_enqueuer.get_enqueuer(), store)
    return SweepCounts(expired, requeued, enqueue_failures, failed)


def cleanup(session: Session, storage) -> CleanupCounts:
    """Expired outputs (app/services/output_cleanup.py); storage is the outputs R2 client. Raises on DB errors."""
    deleted, failed = cleanup_expired_outputs(session, storage)
    return CleanupCounts(deleted, failed)
