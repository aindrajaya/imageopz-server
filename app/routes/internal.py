"""
Internal endpoints called by trusted services: Cloud Tasks (Go payments ingress; job enqueuer, ADR-005) and Cloud
Scheduler (maintenance, ADR-005 amendment). Mounted only when SERVICE_ROLE is worker or all. Not public API.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.models import db, get_db
from app.services import image_service, maintenance
from app.services.payments import apply_lemonsqueezy_body
from app.utils.internal_auth import require_internal_auth
from app.workers.job_worker import JobWorker, Outcome

# include_in_schema=False: every /internal route stays out of the public OpenAPI schema.
router = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_auth)], include_in_schema=False
)
logger = logging.getLogger("imageopz.routes.internal")


@router.post("/payments/events")
async def apply_payment_event(request: Request, db_session: Session = Depends(get_db)):
    """
    Body: the original LemonSqueezy webhook JSON (signature already verified by the ingress).
    200 {"status": "processed"|"duplicate"|"ignored"}; 401 auth; 500 retriable (generic body).
    Events that cannot be applied (malformed, unhandled) are acknowledged as "ignored" so they are not retried.
    """
    body = await request.body()
    try:
        result = apply_lemonsqueezy_body(db_session, body)
    except Exception:
        logger.exception("Internal payment event failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Payment event failed")
    return {"status": result.outcome}


def _process(job_id: str) -> Outcome:
    session = db.get_session()
    try:
        return JobWorker(session).run(job_id)
    finally:
        session.close()


@router.post("/jobs/process")
async def process_job(request: Request):
    """
    Body: {"job_id": "<uuid>"} (Cloud Tasks task from app/services/job_enqueuer.py).
    200 {"status": done|failed|skipped|unknown|ignored} for every terminal outcome, including duplicate deliveries and
    unknown jobs, so Cloud Tasks does not retry them; 503 (generic body) for retriable failures and for a job
    PROCESSING under a live lease (retried until it finishes or the lease expires); 401 auth.
    """
    try:
        job_id = json.loads(await request.body()).get("job_id")
    except Exception:
        job_id = None
    if not isinstance(job_id, str) or not job_id or len(job_id) > 64:
        logger.warning("Job task with a malformed body acknowledged")
        return {"status": "ignored"}
    try:
        outcome = await run_in_threadpool(_process, job_id)
    except Exception:
        logger.exception("Job %s: processing failed; Cloud Tasks will retry", job_id)
        outcome = Outcome.RETRY
    if outcome == Outcome.RETRY:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "retry"})
    return {"status": outcome.value}


_RETRY = {"status": "retry"}


def _with_session(fn, *args):
    session = db.get_session()
    try:
        return fn(session, *args)
    finally:
        session.close()


@router.post("/maintenance/sweep")
async def maintenance_sweep():
    """
    Cloud Scheduler target; same work as `python -m app.cli.sweep_uploads`, idempotent.
    200 {"status": "ok", expired_uploads, requeued, enqueue_failures, failed}; 503 {"status": "retry"} when the database
    fails or a re-enqueue failed (details are logged, never returned); 401 auth.
    """
    try:
        counts = await run_in_threadpool(_with_session, maintenance.sweep)
    except Exception:
        logger.exception("Maintenance sweep failed")
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=_RETRY)
    if counts.enqueue_failures:
        logger.error("Maintenance sweep: %d re-enqueue failure(s)", counts.enqueue_failures)
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=_RETRY)
    return {"status": "ok", **counts.as_dict()}


@router.post("/maintenance/cleanup")
async def maintenance_cleanup():
    """
    Cloud Scheduler target; same work as `python -m app.cli.cleanup_expired`, idempotent.
    200 {"status": "ok", deleted, failed}; 503 {"status": "retry"} when the database fails or an R2 delete failed
    (retried next run); 401 auth.
    """
    try:
        counts = await run_in_threadpool(_with_session, maintenance.cleanup, image_service.r2_storage)
    except Exception:
        logger.exception("Maintenance cleanup failed")
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=_RETRY)
    if counts.failed:
        logger.error("Maintenance cleanup: %d R2 delete failure(s)", counts.failed)
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=_RETRY)
    return {"status": "ok", **counts.as_dict()}
