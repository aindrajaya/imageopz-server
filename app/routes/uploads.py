"""
Presigned direct-to-R2 uploads (T-006b, contract: docs/engineering/contracts/uploads-api.md).
presign: validate against the tier, reserve credits, create UPLOADING jobs, commit, then sign PUT URLs.
complete: verify each uploaded object, move jobs to PENDING or REJECTED, commit, then enqueue.
"""

from datetime import datetime, timedelta
from typing import Optional
import logging
import uuid

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CreditPool, Job, JobStatus, UsersQuota, get_db
from app.schemas import (
    CompletedJob,
    CompleteRequest,
    CompleteResponse,
    ErrorResponse,
    PresignedUpload,
    PresignRequest,
    PresignResponse,
)
from app.services import upload_store, uploads
from app.services.quota_context import activation_pending_response, resolve_quota_context, set_session_cookie
from app.utils.auth_deps import get_optional_supabase_identity
from app.utils.security import QuotaManager, SecurityValidator
from app.utils.supabase_auth import SupabaseIdentity
from app.workers.job_worker import job_queue

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])
logger = logging.getLogger("imageopz.routes.uploads")

# R2 rejects a PUT whose body length differs from the signed Content-Length (403, phase 0 spike in
# tasks/T-006b-presigned-uploads.md), so the declared size is enforced at upload time; HEAD at /complete re-checks.
BIND_CONTENT_LENGTH = True

_ERRORS = {
    400: {"model": ErrorResponse, "description": "Invalid request"},
    413: {"model": ErrorResponse, "description": "File too large"},
    429: {"model": ErrorResponse, "description": "Quota exceeded or activation pending"},
    503: {"model": ErrorResponse, "description": "Uploads storage or job queue unavailable"},
}


def _error(code: int, error: str, message: str, session_id: Optional[str] = None, details: Optional[dict] = None) -> JSONResponse:
    res = JSONResponse(status_code=code, content=ErrorResponse(error=error, message=message, details=details).model_dump())
    if session_id:
        set_session_cookie(res, session_id)
    return res


@router.post("/presign", response_model=PresignResponse, status_code=status.HTTP_201_CREATED, responses=_ERRORS)
async def presign_uploads(
    request: Request,
    response: Response,
    body: PresignRequest,
    session_id_header: Optional[str] = Header(None, alias="X-Session-Id"),
    identity: Optional[SupabaseIdentity] = Depends(get_optional_supabase_identity),
    db_session: Session = Depends(get_db),
):
    """Reserve one credit per file and return a presigned PUT URL per file (expires after UPLOAD_URL_TTL_SECONDS)."""
    files = body.files
    for f in files:
        if f.content_type.lower() not in SecurityValidator.ALLOWED_MIME_TYPES or not SecurityValidator.validate_file_extension(f.name):
            return _error(400, "UNSUPPORTED_TYPE", f"Unsupported file type: {f.name} ({f.content_type})")
    try:
        store = upload_store.get_upload_store()
    except upload_store.UploadStoreNotConfigured:
        logger.error("Uploads bucket not configured")
        return _error(503, "UPLOADS_UNAVAILABLE", "Uploads are temporarily unavailable")

    ctx = resolve_quota_context(
        request=request,
        response=response,
        db_session=db_session,
        identity=identity,
        session_id_header=session_id_header,
        auth_token=body.auth_token,
    )
    sid = ctx.session_id
    pending = activation_pending_response(db_session, ctx, identity, len(files))
    if pending is not None:
        set_session_cookie(pending, sid)
        return pending

    quota = ctx.user_quota
    pool = CreditPool.PRO.value if ctx.effective_is_pro else CreditPool.FREE.value
    if pool == CreditPool.PRO.value:
        ok, msg = QuotaManager.validate_pro_tier_quota(0, len(files), quota.pro_expiry_date)
    else:
        ok, msg = QuotaManager.validate_free_tier_quota(0, len(files), 0, credits_requested=0)
    if not ok:
        return _error(429, "QUOTA_EXCEEDED", msg, sid)
    limit = uploads.max_file_size(pool)
    for f in files:
        if f.size > limit:
            return _error(413, "FILE_TOO_LARGE", f"{f.name}: file size exceeds tier limit of {limit:,} bytes", sid)

    quota_id = quota.id
    if not uploads.reserve_credits(db_session, quota_id, pool, len(files)):
        db_session.rollback()
        fresh = db_session.get(UsersQuota, quota_id)
        if pool == CreditPool.PRO.value:
            _, msg = QuotaManager.validate_pro_tier_quota(0, len(files), fresh.pro_expiry_date, fresh.pro_credits_used or 0, len(files))
        else:
            _, msg = QuotaManager.validate_free_tier_quota(0, len(files), fresh.credits_used or 0, len(files))
        return _error(429, "QUOTA_EXCEEDED", msg or "Quota exceeded", sid)

    now = datetime.utcnow()
    is_pro = pool == CreditPool.PRO.value
    complete_by = now + timedelta(seconds=settings.upload_complete_window_seconds)
    jobs = []
    for f in files:
        job_id = str(uuid.uuid4())
        jobs.append(Job(
            id=job_id,
            session_id=sid or ctx.ip_hash,
            user_quota_id=quota_id,
            status=JobStatus.UPLOADING,
            original_filename=f.name,
            original_size=f.size,
            quality_setting=settings.pro_tier_quality if is_pro else settings.free_tier_quality,
            created_at=now,
            expiry_date=now + timedelta(days=settings.pro_tier_expiry_days if is_pro else settings.free_tier_expiry_days),
            source_object_key=f"{settings.upload_key_prefix}{job_id}",
            declared_size_bytes=f.size,
            declared_content_type=f.content_type.lower(),
            upload_expires_at=complete_by,
            credit_pool=pool,
        ))
    db_session.add_all(jobs)
    db_session.commit()

    ttl = settings.upload_url_ttl_seconds
    try:
        signed = [
            PresignedUpload(
                job_id=job.id,
                object_key=job.source_object_key,
                upload_url=store.presign_put(
                    job.source_object_key,
                    job.declared_content_type,
                    ttl,
                    content_length=job.declared_size_bytes if BIND_CONTENT_LENGTH else None,
                ),
                headers={"Content-Type": job.declared_content_type},
                expires_at=now + timedelta(seconds=ttl),
            )
            for job in jobs
        ]
    except Exception:
        logger.exception("Presigning failed; releasing reserved credits")
        for job in jobs:
            uploads.reject_upload(db_session, job, uploads.PRESIGN_FAILED, now)
        db_session.commit()
        return _error(503, "UPLOADS_UNAVAILABLE", "Uploads are temporarily unavailable", sid)

    return PresignResponse(files=signed, complete_by=complete_by)


def _job_states(db_session: Session, job_ids: list) -> list:
    db_session.expire_all()
    rows = {j.id: j for j in db_session.query(Job).filter(Job.id.in_(job_ids)).all()}
    return [
        CompletedJob(
            job_id=jid,
            status=rows[jid].status.value,
            reason=rows[jid].rejection_reason if rows[jid].status == JobStatus.REJECTED else None,
        )
        for jid in job_ids
    ]


@router.post(
    "/complete",
    response_model=CompleteResponse,
    responses={404: {"model": ErrorResponse, "description": "Unknown job id"}, **_ERRORS},
)
async def complete_uploads(
    request: Request,
    response: Response,
    body: CompleteRequest,
    session_id_header: Optional[str] = Header(None, alias="X-Session-Id"),
    identity: Optional[SupabaseIdentity] = Depends(get_optional_supabase_identity),
    db_session: Session = Depends(get_db),
):
    """Verify uploaded objects and queue them. Idempotent: jobs no longer UPLOADING report their current state."""
    job_ids = list(dict.fromkeys(body.job_ids))
    try:
        store = upload_store.get_upload_store()
    except upload_store.UploadStoreNotConfigured:
        logger.error("Uploads bucket not configured")
        return _error(503, "UPLOADS_UNAVAILABLE", "Uploads are temporarily unavailable")

    ctx = resolve_quota_context(
        request=request,
        response=response,
        db_session=db_session,
        identity=identity,
        session_id_header=session_id_header,
    )
    sid = ctx.session_id
    max_jobs = settings.pro_tier_max_bulk_images if ctx.effective_is_pro else settings.free_tier_max_bulk_images
    if len(job_ids) > max_jobs:
        return _error(400, "TOO_MANY_JOBS", f"Maximum {max_jobs} job ids per request for this tier", sid)

    quota_id = ctx.user_quota.id
    jobs = {j.id: j for j in db_session.query(Job).filter(Job.id.in_(job_ids)).all()}
    unknown = [jid for jid in job_ids if jid not in jobs or jobs[jid].user_quota_id != quota_id]
    if unknown:
        return _error(404, "JOB_NOT_FOUND", "Unknown job id(s)", sid, details={"job_ids": unknown})

    # Storage reads first (no DB writes), then all transitions in one transaction.
    now = datetime.utcnow()
    verdicts = {}
    storage_errors = False
    for jid in job_ids:
        if jobs[jid].status != JobStatus.UPLOADING:
            continue
        try:
            verdicts[jid] = uploads.check_uploaded_object(store, jobs[jid], now)
        except Exception:
            logger.exception("Upload verification failed for job %s; left UPLOADING", jid)
            storage_errors = True

    accepted, rejected_keys = [], []
    for jid, reason in sorted(verdicts.items()):  # job-id lock order, same as the sweeper
        if reason is None:
            if uploads.accept_upload(db_session, jid):
                accepted.append(jid)
        elif uploads.reject_upload(db_session, jobs[jid], reason, now):
            rejected_keys.append(jobs[jid].source_object_key)
    db_session.commit()

    # Commit before enqueue (issues/RACE_CONDITION_FIX.md); jobs that fail to enqueue go back to UPLOADING.
    not_enqueued = [jid for jid in accepted if not job_queue.enqueue_job(jid)]
    if not_enqueued:
        for jid in not_enqueued:
            uploads.revert_to_uploading(db_session, jid)
        db_session.commit()

    for key in rejected_keys:
        uploads.delete_source_best_effort(store, key)

    states = _job_states(db_session, job_ids)
    if not_enqueued or storage_errors:
        error = "QUEUE_UNAVAILABLE" if not_enqueued else "STORAGE_UNAVAILABLE"
        return _error(
            503,
            error,
            "Some uploads could not be queued; retry /uploads/complete for jobs still 'uploading'",
            sid,
            details={"jobs": [s.model_dump(mode="json") for s in states]},
        )
    return CompleteResponse(jobs=states)
