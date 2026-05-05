"""  """"""
Session-related endpoints.

Includes restore/relink flow so users can regain Pro access after storage reset or device changes.
"""

from datetime import datetime
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, UsersQuota, get_db
from app.schemas import SessionRestoreRequest, SessionRestoreResponse
from app.utils.security import IpFingerprint


logger = logging.getLogger("imageopz.routes.session")
router = APIRouter(prefix="/api/v1", tags=["session"])


def _get_or_create_session_id(request: Request, session_id_header: Optional[str]) -> str:
    cookie_name = settings.session_cookie_name
    session_id = (session_id_header or request.cookies.get(cookie_name) or "").strip()
    return session_id or str(uuid.uuid4())


def _set_session_cookie(response: Response, session_id: str) -> None:
    max_age_seconds = int(settings.session_cookie_max_age_days) * 24 * 60 * 60
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=max_age_seconds,
        httponly=False,
        samesite="lax",
        secure=(settings.environment == "production"),
        path="/",
    )


@router.post(
    "/session/restore",
    response_model=SessionRestoreResponse,
    status_code=status.HTTP_200_OK,
)
async def restore_pro_session(
    request: Request,
    response: Response,
    body: SessionRestoreRequest,
    session_id_header: Optional[str] = Header(None, alias="X-Session-Id"),
    db_session: Session = Depends(get_db),
):
    """
    Restore/Relink a browser `session_id` to an existing Pro entitlement.

    - UI calls this when user clicks "Restore Pro" and provides the purchase email.
    - Backend finds an active Pro UsersQuota for that email and binds it to the current `session_id`.
    """
    session_id = _get_or_create_session_id(request, session_id_header)
    _set_session_cookie(response, session_id)
    ip_hash = IpFingerprint.get_ip_hash(request)

    user = db_session.query(User).filter_by(email=body.email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email not found")

    now = datetime.utcnow()
    pro_quota = (
        db_session.query(UsersQuota)
        .filter(
            UsersQuota.user_id == user.id,
            UsersQuota.is_pro == True,  # noqa: E712
            UsersQuota.pro_expiry_date != None,  # noqa: E711
            UsersQuota.pro_expiry_date > now,
        )
        .order_by(UsersQuota.pro_expiry_date.desc())
        .first()
    )
    if not pro_quota:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active Pro entitlement for this email")

    target = (
        db_session.query(UsersQuota)
        .filter_by(session_id=session_id)
        .order_by(UsersQuota.updated_at.desc())
        .first()
    )
    if not target:
        target = UsersQuota(
            id=str(uuid.uuid4()),
            user_id=user.id,
            ip_hash=ip_hash,
            session_id=session_id,
            is_pro=True,
            pro_expiry_date=pro_quota.pro_expiry_date,
        )
        db_session.add(target)
    else:
        target.user_id = user.id
        if not target.ip_hash:
            target.ip_hash = ip_hash
        target.is_pro = True
        target.pro_expiry_date = pro_quota.pro_expiry_date

    db_session.commit()
    logger.info("Session restore succeeded for email=%s session_id=%s", body.email, session_id)

    return SessionRestoreResponse(success=True, session_id=session_id, pro_expiry_date=pro_quota.pro_expiry_date)

