"""
API routes for image optimization endpoints.
Handles bulk uploads, job status polling, and webhook processing.
"""

from fastapi import APIRouter, HTTPException, status, Request, Response, UploadFile, File, Form, Depends, Header
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
import uuid
import os
import logging
import httpx

from app.models import Job, JobStatus, UsersQuota, User, Order, OrderStatus, db, get_db
from app.schemas import (
    BulkOptimizeRequest,
    BulkOptimizeResponse,
    OptimizationResultResponse,
    JobStatusResponse,
    ErrorResponse,
    LemonSqueezyWebhookPayload,
    LemonSqueezyCreateCheckoutRequest,
    LemonSqueezyCreateCheckoutResponse,
)
from app.utils.security import (
    SecurityValidator,
    QuotaManager,
    IpFingerprint,
    JWTHandler,
    WebhookValidator
)
from app.workers.job_worker import job_queue
from app.config import settings

router = APIRouter(prefix="/api/v1", tags=["optimization"])
logger = logging.getLogger("imageopz.routes.optimization")

def _get_session_id_from_request(
    *,
    request: Request,
    session_id_header: Optional[str],
    session_id_form: Optional[str],
) -> str:
    cookie_name = settings.session_cookie_name
    session_id = (session_id_form or session_id_header or request.cookies.get(cookie_name) or "").strip()
    return session_id or str(uuid.uuid4())

def _set_session_cookie(response: Response, session_id: str) -> None:
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

def _mask_email(email: str) -> str:
    e = (email or "").strip()
    if "@" not in e:
        return "<invalid>"
    name, domain = e.split("@", 1)
    if len(name) <= 2:
        name_masked = name[:1] + "*"
    else:
        name_masked = name[:1] + "*" * (len(name) - 2) + name[-1:]
    return f"{name_masked}@{domain}"


# ============= Optimize Endpoint =============

@router.post(
    "/optimize",
    response_model=BulkOptimizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Images queued for optimization"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        413: {"model": ErrorResponse, "description": "File too large"},
        429: {"model": ErrorResponse, "description": "Quota exceeded"},
    }
)
async def optimize_images(
    request: Request,
    response: Response,
    images: List[UploadFile] = File(...),
    auth_token: Optional[str] = Form(None),
    session_id_header: Optional[str] = Header(None, alias="X-Session-Id"),
    session_id_form: Optional[str] = Form(None),
    db_session: Session = Depends(get_db)
):
    """
    Optimize multiple images and queue for processing.
    Supports both free and pro tier with respective quotas.
    
    **Free Tier:**
    - Max 3 images per request
    - Max 10MB per image
    - Max 3 credits total
    - 7 day expiry
    
    **Pro Tier:**
    - Max 30 images per request
    - Max 50MB per image
    - Unlimited credits (with active subscription)
    - 40 day expiry
    """
    try:
        os.makedirs(settings.upload_dir, exist_ok=True)

        session_id = _get_session_id_from_request(
            request=request,
            session_id_header=session_id_header,
            session_id_form=session_id_form,
        )
        _set_session_cookie(response, session_id)

        # Get user identifier
        ip_hash = IpFingerprint.get_ip_hash(request)
        
        # Check authentication
        is_pro = False
        pro_expiry = None
        
        if auth_token:
            token_data = JWTHandler.verify_token(auth_token)
            if token_data and "pro_expiry" in token_data:
                is_pro = True
                pro_expiry = datetime.fromisoformat(token_data["pro_expiry"])
        
        # Get or create user quota record
        q = db_session.query(UsersQuota)
        if session_id:
            user_quota = (
                q.filter(UsersQuota.session_id == session_id)
                .order_by(UsersQuota.updated_at.desc())
                .first()
            )
        else:
            user_quota = (
                q.filter(UsersQuota.ip_hash == ip_hash)
                .order_by(UsersQuota.updated_at.desc())
                .first()
            )
        if not user_quota:
            user_quota = UsersQuota(
                id=str(uuid.uuid4()),
                ip_hash=ip_hash,
                session_id=session_id,
                is_pro=is_pro,
                pro_expiry_date=pro_expiry
            )
            db_session.add(user_quota)
            db_session.commit()
        elif is_pro and pro_expiry:
            user_quota.is_pro = True
            user_quota.pro_expiry_date = pro_expiry
            if session_id and not user_quota.session_id:
                user_quota.session_id = session_id
            if ip_hash and not user_quota.ip_hash:
                user_quota.ip_hash = ip_hash
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
            if updated:
                db_session.commit()
        
        # Request-level quota checks (avoid partial acceptance)
        if not is_pro:
            is_valid, error_msg = QuotaManager.validate_free_tier_quota(
                file_size=0,
                files_count=len(images),
                credits_used=user_quota.credits_used,
                credits_requested=len(images),
            )
            if not is_valid:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=error_msg
                )

        results = []
        
        # Process each image
        for image_file in images:
            try:
                job_id = str(uuid.uuid4())

                # Stream upload to disk (avoid loading into RAM)
                upload_path = os.path.join(settings.upload_dir, f"{job_id}.upload")
                file_size = 0
                head = b""

                max_size = (
                    settings.pro_tier_max_file_size
                    if (user_quota.is_pro and user_quota.pro_expiry_date)
                    else settings.free_tier_max_file_size
                )

                with open(upload_path, "wb") as out:
                    while True:
                        chunk = await image_file.read(1024 * 1024)
                        if not chunk:
                            break
                        if len(head) < 4096:
                            need = 4096 - len(head)
                            head += chunk[:need]
                        file_size += len(chunk)
                        if file_size > max_size:
                            out.close()
                            try:
                                os.remove(upload_path)
                            except Exception:
                                pass
                            raise HTTPException(
                                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                detail=f"File size exceeds tier limit of {max_size:,} bytes"
                            )
                        out.write(chunk)
                
                # Validate extension
                if not SecurityValidator.validate_file_extension(image_file.filename):
                    try:
                        os.remove(upload_path)
                    except Exception:
                        pass
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Unsupported file type: {image_file.filename}"
                    )
                
                # Validate magic number
                sniffed_mime = SecurityValidator.sniff_mime_type(head) if head else None
                valid, mime_or_error = SecurityValidator.validate_magic_number(head or b"0000")
                if not valid:
                    try:
                        os.remove(upload_path)
                    except Exception:
                        pass
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Invalid image format: {mime_or_error}"
                    )

                if sniffed_mime and sniffed_mime not in SecurityValidator.ALLOWED_MIME_TYPES:
                    try:
                        os.remove(upload_path)
                    except Exception:
                        pass
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Unsupported MIME type: {sniffed_mime}"
                    )
                
                # Validate OOM protection
                SecurityValidator.validate_decompression_bomb_protection_path(upload_path)
                
                # Check quotas
                if user_quota.is_pro and user_quota.pro_expiry_date:
                    is_valid, error_msg = QuotaManager.validate_pro_tier_quota(
                        file_size,
                        len(images),
                        user_quota.pro_expiry_date
                    )
                    if not is_valid:
                        try:
                            os.remove(upload_path)
                        except Exception:
                            pass
                        raise HTTPException(
                            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=error_msg
                        )
                    quality = settings.pro_tier_quality
                    expiry_days = settings.pro_tier_expiry_days
                else:
                    is_valid, error_msg = QuotaManager.validate_free_tier_quota(
                        file_size,
                        len(images),
                        user_quota.credits_used,
                        credits_requested=1,
                    )
                    if not is_valid:
                        try:
                            os.remove(upload_path)
                        except Exception:
                            pass
                        raise HTTPException(
                            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=error_msg
                        )
                    quality = settings.free_tier_quality
                    expiry_days = settings.free_tier_expiry_days
                
                # Create job
                expiry_date = datetime.utcnow() + timedelta(days=expiry_days)
                
                job = Job(
                    id=job_id,
                    session_id=session_id or ip_hash,
                    user_quota_id=user_quota.id,
                    status=JobStatus.PENDING,
                    original_filename=image_file.filename,
                    original_size=file_size,
                    quality_setting=quality,
                    created_at=datetime.utcnow(),
                    expiry_date=expiry_date,
                    upload_path=upload_path,
                )
                
                db_session.add(job)
                db_session.flush()
                
                # Enqueue job
                if not job_queue.enqueue_job(job_id):
                    try:
                        os.remove(upload_path)
                    except Exception:
                        pass
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Job queue unavailable (Redis not connected)"
                    )
                
                # Update quota
                if not user_quota.is_pro:
                    user_quota.credits_used += 1
                
                results.append(OptimizationResultResponse(
                    job_id=job_id,
                    filename=image_file.filename,
                    status=JobStatus.PENDING
                ))
            
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Error processing {image_file.filename}: {str(e)}"
                )
        
        db_session.commit()
        
        return BulkOptimizeResponse(
            success=True,
            results=results,
            message=f"Successfully queued {len(results)} image(s) for optimization"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


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

        return LemonSqueezyCreateCheckoutResponse(
            success=True,
            checkout_id=str(checkout_id),
            checkout_url=str(checkout_url),
            ip_hash=ip_hash,
            session_id=session_id,
            provider="lemonsqueezy",
        )
    except HTTPException:
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
        
        progress = job_queue.get_job_progress(job_id) if job_queue else None

        return JobStatusResponse(
            job_id=job.id,
            status=job.status.value,
            original_filename=job.original_filename,
            original_size=job.original_size,
            optimized_size=job.optimized_size,
            compression_ratio=job.compression_ratio,
            r2_url=job.r2_url,
            error_message=job.processing_error,
            created_at=job.created_at,
            expiry_date=job.expiry_date,
            progress=progress.get("progress") if progress else None,
            progress_status=progress.get("status") if progress else None,
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
        
        # Verify signature
        if not WebhookValidator.verify_lemonsqueezy_signature(payload.decode(), signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature"
            )
        
        # Parse payload
        import json
        data = json.loads(payload)
        meta = data.get("meta", {}) or {}
        event_name = data.get("event_name") or meta.get("event_name")
        event_data = data.get("data", {}) or {}

        # Extract a stable external order id + email (best-effort; payload structures vary)
        external_id = (
            str(event_data.get("id"))
            if event_data.get("id") is not None
            else str(data.get("id")) if data.get("id") is not None else None
        )
        attributes = event_data.get("attributes", {}) or {}
        user_email = (
            attributes.get("user_email")
            or attributes.get("customer_email")
            or attributes.get("email")
            or (meta.get("custom_data", {}) or {}).get("email")
        )

        # Idempotently store the order event for support/audit (commit even if we don't activate pro)
        order = None
        if external_id:
            order = (
                db_session.query(Order)
                .filter_by(provider="lemonsqueezy", external_id=external_id)
                .first()
            )
            if not order:
                order = Order(provider="lemonsqueezy", external_id=external_id)
                db_session.add(order)

            order.event_name = str(event_name) if event_name is not None else None
            order.email = user_email
            order.raw_payload_json = payload.decode("utf-8", errors="replace")

            paid_events = {"order_created", "order:created"}
            order.status = OrderStatus.PAID.value if (event_name in paid_events) else OrderStatus.UNKNOWN.value
            db_session.commit()
        
        # Handle order_created event (successful payment)
        paid_events = {"order_created", "order:created"}
        if event_name in paid_events:
            # Best-effort mapping back to an existing free-tier user record.
            # Recommended: include `meta.custom_data.ip_hash` or `meta.custom_data.session_id`
            # when creating the LemonSqueezy checkout.
            custom = meta.get("custom_data", {}) or {}
            ip_hash = custom.get("ip_hash")
            session_id = custom.get("session_id")

            pro_expiry = datetime.utcnow() + timedelta(days=settings.pro_tier_expiry_days)
            user = None
            if user_email:
                user = db_session.query(User).filter_by(email=user_email).first()
                if not user:
                    user = User(email=user_email)
                    db_session.add(user)
                    db_session.flush()

            # Link order to user if possible
            if order and user:
                order.user_id = user.id

            # Create/update quota row for this paid user (or best-effort by ip/session)
            user_quota = None
            if user:
                user_quota = db_session.query(UsersQuota).filter_by(user_id=user.id).first()
            if not user_quota and ip_hash:
                user_quota = db_session.query(UsersQuota).filter_by(ip_hash=ip_hash).first()
            if not user_quota and session_id:
                user_quota = db_session.query(UsersQuota).filter_by(session_id=session_id).first()

            if not user_quota:
                user_quota = UsersQuota(
                    id=str(uuid.uuid4()),
                    user_id=user.id if user else None,
                    ip_hash=ip_hash,
                    session_id=session_id,
                    is_pro=True,
                    pro_expiry_date=pro_expiry,
                )
                db_session.add(user_quota)
            else:
                if user and not user_quota.user_id:
                    user_quota.user_id = user.id
                user_quota.is_pro = True
                user_quota.pro_expiry_date = pro_expiry

            # Extend expiry window for existing jobs for this user (best-effort)
            try:
                db_session.query(Job).filter(
                    Job.user_quota_id == user_quota.id,
                    Job.deleted_at == None,
                ).update({Job.expiry_date: pro_expiry}, synchronize_session=False)
            except Exception:
                pass

            db_session.commit()

            # Return a token useful for manual testing; client flows usually deliver tokens elsewhere.
            token = JWTHandler.generate_token(user_quota.id)
            return {"success": True, "message": "Pro activated", "access_token": token}
        
        return {"success": True, "message": "Webhook processed"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Webhook processing error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing failed: {str(e)}"
        )
