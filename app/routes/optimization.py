"""
API routes for image optimization endpoints.
Handles checkout, job status polling, and webhook processing.
"""

from fastapi import APIRouter, HTTPException, status, Request, Response, Depends, Header
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta
import uuid
import logging
import httpx

from app.models import (
    Job,
    UsersQuota,
    User,
    Order,
    OrderStatus,
    CheckoutIntent,
    CheckoutIntentStatus,
    db,
    get_db,
)
from app.schemas import (
    JobStatusResponse,
    ErrorResponse,
    LemonSqueezyWebhookPayload,
    LemonSqueezyCreateCheckoutRequest,
    LemonSqueezyCreateCheckoutResponse,
)
from app.utils.security import (
    IpFingerprint,
    JWTHandler,
    WebhookValidator
)
from app.utils.auth_deps import get_optional_supabase_identity
from app.services.quota_context import (
    mask_email as _mask_email,
    set_session_cookie as _set_session_cookie,
)
from app.utils.supabase_auth import SupabaseIdentity
from app.services.payments import apply_lemonsqueezy_body
from app.config import settings

router = APIRouter(prefix="/api/v1", tags=["optimization"])
logger = logging.getLogger("imageopz.routes.optimization")


# ============= LemonSqueezy Checkout (Server-side) =============

@router.post(
    "/checkout/lemonsqueezy",
    response_model=LemonSqueezyCreateCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_lemonsqueezy_checkout(
    request: Request,
    response: Response,
    body: LemonSqueezyCreateCheckoutRequest,
    session_id_header: Optional[str] = Header(None, alias="X-Session-Id"),
    identity: Optional[SupabaseIdentity] = Depends(get_optional_supabase_identity),
    db_session: Session = Depends(get_db),
):
    """
    Create a hosted LemonSqueezy checkout URL.

    Why server-side:
    - keeps `LEMONSQUEEZY_API_KEY` off the browser
    - calculates `ip_hash` from the request seen by the server
    - embeds `meta.custom_data` (via `checkout_data.custom`) for accurate webhook matching
    """
    if not settings.lemonsqueezy_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LEMONSQUEEZY_API_KEY is not configured",
        )

    store_id = (body.store_id or settings.lemonsqueezy_store_id or "").strip()
    variant_id = (body.variant_id or settings.lemonsqueezy_variant_id or "").strip()
    if not store_id or not variant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="store_id and variant_id are required (or set LEMONSQUEEZY_STORE_ID/LEMONSQUEEZY_VARIANT_ID)",
        )

    redirect_url = (body.redirect_url or settings.lemonsqueezy_redirect_url or "").strip() or None
    test_mode = body.test_mode if body.test_mode is not None else settings.lemonsqueezy_test_mode

    session_id = (body.session_id or session_id_header or request.cookies.get(settings.session_cookie_name) or "").strip() or None
    if not session_id:
        session_id = str(uuid.uuid4())
    _set_session_cookie(response, session_id)
    ip_hash = IpFingerprint.get_ip_hash(request)

    lemonsqueezy_payload: dict = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email": body.email,
                    "custom": {
                        "email": body.email,
                        "ip_hash": ip_hash,
                        "session_id": session_id,
                    },
                },
                "test_mode": bool(test_mode),
            },
            "relationships": {
                "store": {"data": {"type": "stores", "id": str(store_id)}},
                "variant": {"data": {"type": "variants", "id": str(variant_id)}},
            },
        }
    }

    # LemonSqueezy validates custom fields strictly; omit optional fields when absent.
    if identity and identity.sub:
        lemonsqueezy_payload["data"]["attributes"]["checkout_data"]["custom"]["supabase_user_id"] = str(identity.sub)

    if redirect_url:
        lemonsqueezy_payload["data"]["attributes"]["product_options"] = {"redirect_url": redirect_url}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                "https://api.lemonsqueezy.com/v1/checkouts",
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                    "Authorization": f"Bearer {settings.lemonsqueezy_api_key}",
                    "User-Agent": "imageopz-server/1.0",
                },
                json=lemonsqueezy_payload,
                follow_redirects=True,
            )
        if res.status_code >= 400:
            # LemonSqueezy uses JSON:API error responses.
            detail = f"LemonSqueezy API error: {res.status_code}"
            ls_request_id = res.headers.get("apigw-requestid") or res.headers.get("x-request-id")
            cf_ray = res.headers.get("cf-ray")
            try:
                j = res.json()
                errs = j.get("errors") if isinstance(j, dict) else None
                if isinstance(errs, list) and errs:
                    first = errs[0] if isinstance(errs[0], dict) else {}
                    title = first.get("title")
                    err_detail = first.get("detail")
                    if title or err_detail:
                        detail = f"{detail} - {title or ''} {err_detail or ''}".strip()
            except Exception:
                pass

            # Add request correlation hints (safe to expose + log).
            if ls_request_id:
                detail = f"{detail} (ls_request_id={ls_request_id})"
            if cf_ray:
                detail = f"{detail} (cf_ray={cf_ray})"

            if res.status_code == 404:
                detail = (
                    f"{detail}. Common causes: wrong `store_id`/`variant_id`, or API key mode mismatch "
                    f"(test API key with live store ids, or vice versa)."
                )

            # Server-side log with extra context (do not log API key).
            try:
                body_preview = res.text
                if body_preview and len(body_preview) > 1200:
                    body_preview = body_preview[:1200] + "...(truncated)"
                logger.warning(
                    "LemonSqueezy checkout create failed: status=%s store_id=%s variant_id=%s test_mode=%s email=%s session_id=%s ip_hash=%s ls_request_id=%s cf_ray=%s body=%s",
                    res.status_code,
                    store_id,
                    variant_id,
                    bool(test_mode),
                    _mask_email(body.email),
                    (session_id[:12] + "...") if session_id else None,
                    ip_hash,
                    ls_request_id,
                    cf_ray,
                    body_preview,
                )
            except Exception:
                pass

            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

        data = res.json().get("data") or {}
        attrs = data.get("attributes") or {}
        checkout_url = attrs.get("url")
        checkout_id = data.get("id")
        if not checkout_url or not checkout_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="LemonSqueezy response missing checkout url/id",
            )

        # Persist a short-lived checkout intent for activation UX + relinking robustness.
        try:
            now = datetime.utcnow()
            expires_at = now + timedelta(minutes=int(settings.checkout_intent_ttl_minutes))
            intent = CheckoutIntent(
                provider="lemonsqueezy",
                checkout_id=str(checkout_id),
                email=body.email,
                ip_hash=ip_hash,
                session_id=session_id,
                status=CheckoutIntentStatus.CREATED.value,
                expires_at=expires_at,
            )
            db_session.add(intent)
            db_session.commit()
        except Exception:
            # Best-effort only; checkout itself succeeded.
            try:
                db_session.rollback()
            except Exception:
                pass
            logger.exception("Failed to persist checkout intent")

        return LemonSqueezyCreateCheckoutResponse(
            success=True,
            checkout_id=str(checkout_id),
            checkout_url=str(checkout_url),
            ip_hash=ip_hash,
            session_id=session_id,
            provider="lemonsqueezy",
        )
    except HTTPException:
        try:
            db_session.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create checkout: {str(e)}",
        )


# ============= Status Polling Endpoint =============

@router.get(
    "/status/{job_id}",
    response_model=JobStatusResponse,
    responses={
        200: {"description": "Job status retrieved"},
        404: {"model": ErrorResponse, "description": "Job not found"},
    }
)
async def get_job_status(
    job_id: str,
    identity: Optional[SupabaseIdentity] = Depends(get_optional_supabase_identity),
    db_session: Session = Depends(get_db)
):
    """
    Poll job status and get optimization results.
    Returns current status, file sizes, compression ratio, and download URL.
    """
    try:
        job = db_session.query(Job).filter_by(id=job_id).first()
        
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found"
            )

        # If authenticated, restrict job access to the account's quota.
        if identity:
            user = db_session.query(User).filter_by(supabase_user_id=identity.sub).first()
            if not user:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
            quota = db_session.query(UsersQuota).filter_by(id=job.user_quota_id).first()
            if not quota or quota.user_id != user.id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
        
        return JobStatusResponse(
            job_id=job.id,
            status=job.status.value,
            original_filename=job.original_filename,
            original_size=job.original_size,
            original_width=getattr(job, "original_width", None),
            original_height=getattr(job, "original_height", None),
            optimized_size=job.optimized_size,
            compression_ratio=job.compression_ratio,
            r2_url=job.r2_url,
            error_message=job.processing_error,
            rejection_reason=getattr(job, "rejection_reason", None),
            created_at=job.created_at,
            expiry_date=job.expiry_date,
            processing_started_at=getattr(job, "processing_started_at", None),
            processing_finished_at=getattr(job, "processing_finished_at", None),
            processing_duration_ms=getattr(job, "processing_duration_ms", None),
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving job status: {str(e)}"
        )


# ============= LemonSqueezy Webhook =============

@router.post("/webhooks/lemonsqueezy")
async def handle_lemonsqueezy_webhook(
    request: Request,
    db_session: Session = Depends(get_db)
):
    """
    Handle LemonSqueezy payment webhooks.
    Activates pro tier for users after successful payment.
    """
    try:
        # Get payload and signature
        payload = await request.body()
        signature = request.headers.get("X-Signature", "")

        # Verify signature (over the raw bytes, so an undecodable body with a valid signature reaches the service)
        if not WebhookValidator.verify_lemonsqueezy_signature(payload, signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )

        result = apply_lemonsqueezy_body(db_session, payload)

        if result.outcome == "ignored":
            return {"status": "ignored"}
        if result.outcome == "duplicate":
            return {"success": True, "message": "Duplicate event ignored"}
        if result.activated:
            # Return a token useful for manual testing; client flows usually deliver tokens elsewhere.
            token = JWTHandler.generate_token(result.user_quota_id)
            return {"success": True, "message": "Pro activated", "access_token": token}
        return {"success": True, "message": "Webhook processed"}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Webhook processing error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed"
        )
