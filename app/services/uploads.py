"""
Presigned upload lifecycle (T-006b): credit reservation, upload verification and job transitions.

Credits are reserved at presign with one conditional UPDATE and released exactly once: every transition out of
UPLOADING is a conditional UPDATE (WHERE status = 'UPLOADING'), and only the caller whose UPDATE matched releases
the credit, in the same transaction.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PIL import Image
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CreditPool, Job, JobStatus, UsersQuota
from app.services.upload_store import UploadStore
from app.utils.security import SecurityValidator

logger = logging.getLogger("imageopz.uploads")

# Bytes read at /uploads/complete for the magic/MIME sniff and the header-only dimension read (D3).
SNIFF_BYTES = 64 * 1024

# Rejection reasons (jobs.rejection_reason, String(32)); part of the API contract.
EXPIRED = "expired"
MISSING = "missing"
SIZE_MISMATCH = "size_mismatch"
TYPE_MISMATCH = "type_mismatch"
INVALID_IMAGE = "invalid_image"
TOO_MANY_PIXELS = "too_many_pixels"
PRESIGN_FAILED = "presign_failed"


def _credit_column(pool: str):
    return UsersQuota.pro_credits_used if pool == CreditPool.PRO.value else UsersQuota.credits_used


def credit_limit(pool: str) -> int:
    return settings.pro_tier_max_credits if pool == CreditPool.PRO.value else settings.free_tier_max_credits


def max_file_size(pool: str) -> int:
    return settings.pro_tier_max_file_size if pool == CreditPool.PRO.value else settings.free_tier_max_file_size


def reserve_credits(db: Session, quota_id: str, pool: str, n: int) -> bool:
    """Atomically add n credits to the pool's counter if the result stays within the limit (no read-then-write)."""
    col = _credit_column(pool)
    used = func.coalesce(col, 0)
    res = db.execute(
        update(UsersQuota)
        .where(UsersQuota.id == quota_id, used + n <= credit_limit(pool))
        .values({col: used + n})
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1


def _release_credit(db: Session, quota_id: str, pool: Optional[str]) -> None:
    col = _credit_column(pool or CreditPool.FREE.value)
    db.execute(
        update(UsersQuota)
        .where(UsersQuota.id == quota_id, col >= 1)
        .values({col: col - 1})
        .execution_options(synchronize_session=False)
    )


def _transition(db: Session, job_id: str, from_status: JobStatus, **values) -> bool:
    res = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == from_status)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1


def reject_upload(db: Session, job: Job, reason: str, now: datetime) -> bool:
    """UPLOADING -> REJECTED and release the job's credit. False if another caller already moved the job."""
    if not _transition(db, job.id, JobStatus.UPLOADING, status=JobStatus.REJECTED, rejection_reason=reason, processing_finished_at=now):
        return False
    _release_credit(db, job.user_quota_id, job.credit_pool)
    return True


def accept_upload(db: Session, job_id: str, now: datetime) -> bool:
    """UPLOADING -> PENDING; enqueued_at starts the stale-PENDING clock of the sweeper (U8)."""
    return _transition(db, job_id, JobStatus.UPLOADING, status=JobStatus.PENDING, enqueued_at=now)


def revert_to_uploading(db: Session, job_id: str) -> bool:
    """Undo accept_upload when the job could not be enqueued, so a retried /complete re-enqueues it."""
    return _transition(db, job_id, JobStatus.PENDING, status=JobStatus.UPLOADING)


def delete_source_best_effort(store: UploadStore, key: Optional[str]) -> None:
    if not key:
        return
    try:
        store.delete(key)
    except Exception:
        logger.warning("Failed to delete upload object %s (bucket lifecycle rule is the backstop)", key)


def check_uploaded_object(store: UploadStore, job: Job, now: datetime) -> Optional[str]:
    """Rejection reason for an UPLOADING job's object, or None when it may be processed (the worker re-checks)."""
    if job.upload_expires_at is None or now > job.upload_expires_at:
        return EXPIRED
    key = job.source_object_key
    head = store.head(key)
    if head is None:
        return MISSING
    if head.size != job.declared_size_bytes or head.size > max_file_size(job.credit_pool):
        return SIZE_MISMATCH
    if (head.content_type or "").split(";")[0].strip().lower() != job.declared_content_type:
        return TYPE_MISMATCH

    data = store.get_range(key, 0, SNIFF_BYTES - 1)
    if not SecurityValidator.validate_file_extension(job.original_filename or ""):
        return INVALID_IMAGE
    valid, magic_mime = SecurityValidator.validate_magic_number(data)
    if not valid or magic_mime != job.declared_content_type:
        return INVALID_IMAGE
    sniffed = SecurityValidator.sniff_mime_type(data)
    if sniffed and sniffed not in SecurityValidator.ALLOWED_MIME_TYPES:
        return INVALID_IMAGE
    try:
        width, height = SecurityValidator.read_header_dimensions(data, SecurityValidator.pil_formats(job.declared_content_type))
    except Image.DecompressionBombError:
        return TOO_MANY_PIXELS
    except Exception:
        return INVALID_IMAGE
    if width * height > settings.max_image_pixels:
        return TOO_MANY_PIXELS
    return None
