"""
Internal endpoints called by trusted services (Go payments ingress via Cloud Tasks). Not public API.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models import get_db
from app.services.payments import apply_lemonsqueezy_body
from app.utils.internal_auth import require_internal_auth

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_auth)])
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
