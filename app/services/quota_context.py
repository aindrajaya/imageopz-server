"""
Request identity and quota resolution, moved verbatim from optimize_images (T-006b) so other endpoints share it.
Anonymous users are keyed by session id (cookie imageopz_sid or X-Session-Id); Supabase-authenticated users by
their User row. Behavior is unchanged.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import logging
import uuid

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CheckoutIntent, CheckoutIntentStatus, User, UsersQuota
from app.schemas import ErrorResponse
from app.utils.security import IpFingerprint, JWTHandler
from app.utils.supabase_auth import SupabaseIdentity

logger = logging.getLogger("imageopz.routes.optimization")


def get_session_id_from_request(
    *,
    request: Request,
    session_id_header: Optional[str],
    session_id_form: Optional[str],
) -> str:
    cookie_name = settings.session_cookie_name
    session_id = (session_id_form or session_id_header or request.cookies.get(cookie_name) or "").strip()
    return session_id or str(uuid.uuid4())

def set_session_cookie(response: Response, session_id: str) -> None:
    max_age_seconds = int(settings.session_cookie_max_age_days) * 24 * 60 * 60
    # SameSite=Lax so it survives normal navigations; Secure should be true behind HTTPS (prod).
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=max_age_seconds,
        httponly=False,  # frontend may need to read it; can be true if you only use header
        samesite="lax",
        secure=(settings.environment == "production"),
        path="/",
    )

def mask_email(email: str) -> str:
    e = (email or "").strip()
    if "@" not in e:
        return "<invalid>"
    name, domain = e.split("@", 1)
    if len(name) <= 2:
        name_masked = name[:1] + "*"
    else:
        name_masked = name[:1] + "*" * (len(name) - 2) + name[-1:]
    return f"{name_masked}@{domain}"

@dataclass
class QuotaContext:
    session_id: str
    ip_hash: str
    user: Optional[User]
    user_quota: UsersQuota
    effective_is_pro: bool


def resolve_quota_context(
    *,
    request: Request,
    response: Response,
    db_session: Session,
    identity: Optional[SupabaseIdentity],
    session_id_header: Optional[str],
    session_id_form: Optional[str] = None,
    auth_token: Optional[str] = None,
) -> QuotaContext:
    """Resolve session id (and set the cookie), the User for a Supabase identity, and the UsersQuota row."""
    session_id = get_session_id_from_request(
        request=request,
        session_id_header=session_id_header,
        session_id_form=session_id_form,
    )
    set_session_cookie(response, session_id)

    # Get user identifier
    ip_hash = IpFingerprint.get_ip_hash(request)

    # Check authentication (optional JWT path)
    token_is_pro = False
    token_pro_expiry: Optional[datetime] = None

    if auth_token:
        token_data = JWTHandler.verify_token(auth_token)
        if token_data and "pro_expiry" in token_data:
            try:
                token_is_pro = True
                token_pro_expiry = datetime.fromisoformat(token_data["pro_expiry"])
            except Exception:
                token_is_pro = False
                token_pro_expiry = None

    # If authenticated, ensure User exists and use user-based quota.
    user: Optional[User] = None
    if identity:
        user = db_session.query(User).filter_by(supabase_user_id=identity.sub).first()
        if not user:
            if not identity.email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Supabase token missing email claim; cannot create user record",
                )
            # Best-effort link by email first
            user = db_session.query(User).filter_by(email=identity.email).first()
            if user and user.supabase_user_id and user.supabase_user_id != identity.sub:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email is already linked to another Supabase user id",
                )
            if user:
                user.supabase_user_id = identity.sub
            else:
                user = User(email=identity.email, supabase_user_id=identity.sub)
                db_session.add(user)
            db_session.flush()

    # Get or create user quota record
    q = db_session.query(UsersQuota)
    if user:
        user_quota = (
            q.filter(UsersQuota.user_id == user.id)
            .order_by(UsersQuota.updated_at.desc())
            .first()
        )
    elif session_id:
        user_quota = (
            q.filter(UsersQuota.session_id == session_id)
            .order_by(UsersQuota.updated_at.desc())
            .first()
        )
    else:
        # Guest matching should always have a session_id (cookie/header); fallback to new session id earlier.
        user_quota = None
    if not user_quota:
        user_quota = UsersQuota(
            id=str(uuid.uuid4()),
            user_id=user.id if user else None,
            ip_hash=ip_hash,
            session_id=session_id,
            is_pro=token_is_pro,
            pro_expiry_date=token_pro_expiry,
            pro_verified=bool(token_is_pro and token_pro_expiry),
            pro_source="webhook_session" if (token_is_pro and token_pro_expiry) else "none",
        )
        db_session.add(user_quota)
        db_session.commit()
    elif token_is_pro and token_pro_expiry:
        user_quota.is_pro = True
        user_quota.pro_expiry_date = token_pro_expiry
        user_quota.pro_verified = True
        user_quota.pro_source = "webhook_session"
        if session_id and not user_quota.session_id:
            user_quota.session_id = session_id
        if ip_hash and not user_quota.ip_hash:
            user_quota.ip_hash = ip_hash
        if user and not user_quota.user_id:
            user_quota.user_id = user.id
        db_session.commit()
    else:
        # Best-effort backfill identifiers for matching (webhook may carry session_id/ip_hash)
        updated = False
        if session_id and not user_quota.session_id:
            user_quota.session_id = session_id
            updated = True
        if ip_hash and not user_quota.ip_hash:
            user_quota.ip_hash = ip_hash
            updated = True
        if user and not user_quota.user_id:
            user_quota.user_id = user.id
            updated = True
        if updated:
            db_session.commit()

    # Guest-only: attempt to "rehydrate" Pro from a recently-paid checkout intent.
    # This handles cases where `session_id` changes between checkout -> post-purchase optimize,
    # but webhook already activated Pro for the purchase email.
    if not identity:
        try:
            now = datetime.utcnow()
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
                user_quota.is_pro
                and user_quota.pro_expiry_date is not None
                and user_quota.pro_expiry_date > now
            ):
                paid_user = db_session.query(User).filter_by(email=paid_intent.email).first()
                if paid_user:
                    pro_quota = (
                        db_session.query(UsersQuota)
                        .filter(
                            UsersQuota.user_id == paid_user.id,
                            UsersQuota.is_pro == True,  # noqa: E712
                            UsersQuota.pro_expiry_date != None,  # noqa: E711
                            UsersQuota.pro_expiry_date > now,
                        )
                        .order_by(UsersQuota.pro_expiry_date.desc())
                        .first()
                    )
                    if pro_quota and pro_quota.pro_expiry_date:
                        user_quota.user_id = paid_user.id
                        user_quota.is_pro = True
                        user_quota.pro_expiry_date = pro_quota.pro_expiry_date
                        user_quota.pro_verified = True
                        user_quota.pro_source = "session"
                        if not user_quota.session_id:
                            user_quota.session_id = session_id
                        if not user_quota.ip_hash:
                            user_quota.ip_hash = ip_hash
                        if getattr(user_quota, "pro_credits_used", None) is None:
                            user_quota.pro_credits_used = int(getattr(pro_quota, "pro_credits_used", 0) or 0)
                        db_session.commit()
                        logger.info(
                            "Rehydrated Pro from paid intent. email=%s session_id=%s",
                            mask_email(paid_intent.email),
                            session_id,
                        )
        except Exception:
            # best-effort only
            try:
                db_session.rollback()
            except Exception:
                pass

    # Determine effective pro status (webhook-activated quota should work without auth_token)
    now = datetime.utcnow()
    quota_is_pro = bool(
        user_quota.is_pro
        and user_quota.pro_expiry_date is not None
        and user_quota.pro_expiry_date > now
    )
    effective_is_pro = bool(
        (token_is_pro and token_pro_expiry and token_pro_expiry > now) or quota_is_pro
    )
    return QuotaContext(
        session_id=session_id,
        ip_hash=ip_hash,
        user=user,
        user_quota=user_quota,
        effective_is_pro=effective_is_pro,
    )


def activation_pending_response(
    db_session: Session, ctx: QuotaContext, identity: Optional[SupabaseIdentity], files_count: int
) -> Optional[JSONResponse]:
    """429 ACTIVATION_PENDING when a guest asks for more than the free batch right after starting a checkout."""
    if ctx.effective_is_pro or identity or files_count <= settings.free_tier_max_bulk_images:
        return None
    now = datetime.utcnow()
    session_id, ip_hash = ctx.session_id, ctx.ip_hash
    try:
        pending = (
            db_session.query(CheckoutIntent)
            .filter(
                CheckoutIntent.provider == "lemonsqueezy",
                CheckoutIntent.status == CheckoutIntentStatus.CREATED.value,
                CheckoutIntent.expires_at != None,  # noqa: E711
                CheckoutIntent.expires_at > now,
            )
            .filter((CheckoutIntent.session_id == session_id) | (CheckoutIntent.ip_hash == ip_hash))
            .order_by(CheckoutIntent.created_at.desc())
            .first()
        )
        if pending:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content=ErrorResponse(
                    error="ACTIVATION_PENDING",
                    message="Checkout started recently; Pro activation may still be processing. Please retry shortly.",
                    details={"retry_after_ms": int(settings.activation_pending_retry_ms or 1500)},
                ).model_dump(),
            )
    except Exception:
        pass
    return None
