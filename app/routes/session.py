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
from app.models import User, UsersQuota, CheckoutIntent, CheckoutIntentStatus, get_db
from app.schemas import SessionRestoreRequest, SessionRestoreResponse, SessionMeResponse, SessionLogoutResponse
from app.utils.security import IpFingerprint
from app.utils.auth_deps import get_optional_supabase_identity
from app.utils.supabase_auth import SupabaseIdentity


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

def _clear_session_cookie(response: Response) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value="",
        max_age=0,
        expires=0,
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
    identity: Optional[SupabaseIdentity] = Depends(get_optional_supabase_identity),
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

    # If authenticated, link this email record to supabase_user_id (unique).
    if identity:
        existing = db_session.query(User).filter_by(supabase_user_id=identity.sub).first()
        if existing and existing.id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Supabase user id is already linked to another user",
            )
        user.supabase_user_id = identity.sub

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

    # If authenticated, bind pro to the user_id quota; else bind to session_id quota.
    if identity:
        target = (
            db_session.query(UsersQuota)
            .filter_by(user_id=user.id)
            .order_by(UsersQuota.updated_at.desc())
            .first()
        )
    else:
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
            pro_credits_used=int(getattr(pro_quota, "pro_credits_used", 0) or 0),
            pro_verified=True,
            pro_source="restore",
        )
        db_session.add(target)
    else:
        target.user_id = user.id
        if not target.ip_hash:
            target.ip_hash = ip_hash
        target.is_pro = True
        target.pro_expiry_date = pro_quota.pro_expiry_date
        # Keep existing pro credit usage if any
        if getattr(target, "pro_credits_used", None) is None:
            target.pro_credits_used = int(getattr(pro_quota, "pro_credits_used", 0) or 0)
        target.pro_verified = True
        target.pro_source = "restore"

    db_session.commit()
    logger.info("Session restore succeeded for email=%s session_id=%s", body.email, session_id)

    return SessionRestoreResponse(success=True, session_id=session_id, pro_expiry_date=pro_quota.pro_expiry_date)


@router.get(
    "/session/me",
    response_model=SessionMeResponse,
    status_code=status.HTTP_200_OK,
)
async def get_session_me(
    request: Request,
    response: Response,
    session_id_header: Optional[str] = Header(None, alias="X-Session-Id"),
    identity: Optional[SupabaseIdentity] = Depends(get_optional_supabase_identity),
    db_session: Session = Depends(get_db),
):
    """
    Return the server-side session state needed by the frontend UI.

    This is the authoritative source for:
    - whether the current session is Pro (active)
    - free-tier credits used/remaining
    """
    session_id = _get_or_create_session_id(request, session_id_header)
    _set_session_cookie(response, session_id)
    ip_hash = IpFingerprint.get_ip_hash(request)

    user = None
    quota = None
    if identity:
        # Authenticated policy: account-only. Do NOT fall back to session/ip.
        user = db_session.query(User).filter_by(supabase_user_id=identity.sub).first()
        if user:
            quota = (
                db_session.query(UsersQuota)
                .filter(UsersQuota.user_id == user.id)
                .order_by(UsersQuota.updated_at.desc())
                .first()
            )
    else:
        # Guest policy: session_id-first. Do NOT treat ip_hash as a durable identity for Pro/quota.
        quota = (
            db_session.query(UsersQuota)
            .filter(UsersQuota.session_id == session_id)
            .order_by(UsersQuota.updated_at.desc())
            .first()
        )

    activation_pending = False
    if not identity:
        try:
            now = datetime.utcnow()
            # If a paid intent exists but current session quota isn't Pro, try to rehydrate by email.
            paid_intent = (
                db_session.query(CheckoutIntent)
                .filter(
                    CheckoutIntent.provider == "lemonsqueezy",
                    CheckoutIntent.status == CheckoutIntentStatus.PAID.value,
                )
                # IMPORTANT: paid intent rehydration must be session_id-only (NOT ip_hash),
                # otherwise other browsers/incognito on the same network could inherit Pro.
                .filter(CheckoutIntent.session_id == session_id)
                .order_by(CheckoutIntent.paid_at.desc().nullslast(), CheckoutIntent.created_at.desc())
                .first()
            )
            if paid_intent and paid_intent.email and not (
                quota and getattr(quota, "is_pro", False) and getattr(quota, "pro_expiry_date", None) and getattr(quota, "pro_expiry_date") > now
            ):
                user = db_session.query(User).filter_by(email=paid_intent.email).first()
                if user:
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
                    if pro_quota and pro_quota.pro_expiry_date:
                        # Bind/upgrade the current session quota row (or create one) for immediate access.
                        if not quota:
                            quota = UsersQuota(
                                id=str(uuid.uuid4()),
                                user_id=user.id,
                                ip_hash=ip_hash,
                                session_id=session_id,
                                is_pro=True,
                                pro_expiry_date=pro_quota.pro_expiry_date,
                                pro_credits_used=int(getattr(pro_quota, "pro_credits_used", 0) or 0),
                                pro_verified=True,
                                pro_source="session",
                            )
                            db_session.add(quota)
                        else:
                            quota.user_id = user.id
                            if not quota.ip_hash:
                                quota.ip_hash = ip_hash
                            quota.is_pro = True
                            quota.pro_expiry_date = pro_quota.pro_expiry_date
                            if getattr(quota, "pro_credits_used", None) is None:
                                quota.pro_credits_used = int(getattr(pro_quota, "pro_credits_used", 0) or 0)
                            quota.pro_verified = True
                            quota.pro_source = "session"
                        db_session.commit()
                        masked = (paid_intent.email[:2] + "***") if paid_intent.email else "<none>"
                        logger.info(
                            "Session/me rehydrated Pro from paid intent. email=%s session_id=%s",
                            masked,
                            session_id,
                        )
                    else:
                        # Paid intent exists but entitlement isn't active yet (webhook might still be processing)
                        activation_pending = True

            # If no active pro found yet, check for a recent checkout intent that hasn't been confirmed paid.
            if not (quota and getattr(quota, "is_pro", False) and getattr(quota, "pro_expiry_date", None) and getattr(quota, "pro_expiry_date") > now):
                intent = (
                    db_session.query(CheckoutIntent)
                    .filter(
                        CheckoutIntent.provider == "lemonsqueezy",
                        CheckoutIntent.status == CheckoutIntentStatus.CREATED.value,
                        CheckoutIntent.expires_at != None,  # noqa: E711
                        CheckoutIntent.expires_at > now,
                    )
                    .filter(
                        (CheckoutIntent.session_id == session_id) | (CheckoutIntent.ip_hash == ip_hash)
                    )
                    .order_by(CheckoutIntent.created_at.desc())
                    .first()
                )
                activation_pending = bool(intent is not None)
        except Exception:
            # best-effort only
            activation_pending = False

    credits_used = int(getattr(quota, "credits_used", 0) or 0) if quota else 0
    pro_credits_used = int(getattr(quota, "pro_credits_used", 0) or 0) if quota else 0
    pro_expiry = getattr(quota, "pro_expiry_date", None)
    is_pro = bool(getattr(quota, "is_pro", False) and pro_expiry and pro_expiry > datetime.utcnow())
    pro_verified = bool(getattr(quota, "pro_verified", False)) if quota else False
    raw_source = str(getattr(quota, "pro_source", None) or "") if quota else ""
    if identity and is_pro:
        pro_source = "account"
    elif not is_pro:
        pro_source = "none"
    else:
        # Normalize known sources for UI/debug consistency.
        mapping = {
            "webhook": "webhook_session",
            "intent": "session",
            "session": "session",
            "restore": "restore",
            "webhook_session": "webhook_session",
        }
        pro_source = mapping.get(raw_source, "unknown")
    # If authenticated, missing/invalid pro is a restore-required state.
    if identity:
        needs_restore = not (is_pro and pro_verified)
    else:
        needs_restore = bool(is_pro and not pro_verified)

    if is_pro:
        credits_remaining = -1
        pro_credits_remaining = max(0, settings.pro_tier_max_credits - pro_credits_used)
    else:
        credits_remaining = max(0, settings.free_tier_max_credits - credits_used)
        pro_credits_remaining = -1

    return SessionMeResponse(
        success=True,
        session_id=session_id,
        ip_hash=ip_hash,
        is_authenticated=bool(identity),
        supabase_user_id=identity.sub if identity else None,
        email=(identity.email if identity else (user.email if user else None)),
        is_pro=is_pro,
        pro_expiry_date=pro_expiry if is_pro else None,
        pro_verified=pro_verified,
        pro_source=pro_source,
        needs_restore=needs_restore,
        credits_used=credits_used,
        free_tier_max_credits=settings.free_tier_max_credits,
        credits_remaining=credits_remaining,
        pro_tier_max_credits=settings.pro_tier_max_credits,
        pro_credits_used=pro_credits_used,
        pro_credits_remaining=pro_credits_remaining,
        activation_pending=activation_pending,
        activation_retry_after_ms=(settings.activation_pending_retry_ms if activation_pending else 0),
    )


@router.post(
    "/session/reconcile",
    response_model=SessionMeResponse,
    status_code=status.HTTP_200_OK,
)
async def reconcile_session(
    request: Request,
    response: Response,
    session_id_header: Optional[str] = Header(None, alias="X-Session-Id"),
    identity: Optional[SupabaseIdentity] = Depends(get_optional_supabase_identity),
    db_session: Session = Depends(get_db),
):
    """
    Best-effort reconciliation hook for the frontend after returning from checkout.
    - Does not require login.
    - Attempts to rehydrate Pro access for the current browser session based on recent intents/webhooks.
    """
    return await get_session_me(
        request=request,
        response=response,
        session_id_header=session_id_header,
        identity=identity,
        db_session=db_session,
    )


@router.post(
    "/session/logout",
    response_model=SessionLogoutResponse,
    status_code=status.HTTP_200_OK,
)
async def logout_session(
    request: Request,
    response: Response,
):
    """
    Clear the session cookie and rotate to a new session id.
    Useful when the frontend wants a reliable "reset session" action.
    """
    _clear_session_cookie(response)
    new_session_id = str(uuid.uuid4())
    _set_session_cookie(response, new_session_id)
    return SessionLogoutResponse(success=True, session_id=new_session_id)
