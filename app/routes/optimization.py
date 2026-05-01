"""
API routes for image optimization endpoints.
Handles bulk uploads, job status polling, and webhook processing.
"""

from fastapi import APIRouter, HTTPException, status, Request, UploadFile, File, Form, Depends
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
import uuid

from app.models import Job, JobStatus, UsersQuota, db, get_db
from app.schemas import (
    BulkOptimizeRequest,
    BulkOptimizeResponse,
    OptimizationResultResponse,
    JobStatusResponse,
    ErrorResponse,
    LemonSqueezyWebhookPayload,
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
    images: List[UploadFile] = File(...),
    auth_token: Optional[str] = Form(None),
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
        user_quota = db_session.query(UsersQuota).filter_by(ip_hash=ip_hash).first()
        if not user_quota:
            user_quota = UsersQuota(
                id=str(uuid.uuid4()),
                ip_hash=ip_hash,
                is_pro=is_pro,
                pro_expiry_date=pro_expiry
            )
            db_session.add(user_quota)
            db_session.commit()
        elif is_pro and pro_expiry:
            user_quota.is_pro = True
            user_quota.pro_expiry_date = pro_expiry
            db_session.commit()
        
        results = []
        
        # Process each image
        for image_file in images:
            try:
                # Read file
                file_data = await image_file.read()
                file_size = len(file_data)
                
                # Validate extension
                if not SecurityValidator.validate_file_extension(image_file.filename):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Unsupported file type: {image_file.filename}"
                    )
                
                # Validate magic number
                valid, mime_or_error = SecurityValidator.validate_magic_number(file_data)
                if not valid:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Invalid image format: {mime_or_error}"
                    )
                
                # Validate OOM protection
                SecurityValidator.validate_decompression_bomb_protection(file_data)
                
                # Check quotas
                if user_quota.is_pro and user_quota.pro_expiry_date:
                    is_valid, error_msg = QuotaManager.validate_pro_tier_quota(
                        file_size,
                        len(images),
                        user_quota.pro_expiry_date
                    )
                    if not is_valid:
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
                        user_quota.credits_used
                    )
                    if not is_valid:
                        raise HTTPException(
                            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=error_msg
                        )
                    quality = settings.free_tier_quality
                    expiry_days = settings.free_tier_expiry_days
                
                # Create job
                job_id = str(uuid.uuid4())
                expiry_date = datetime.utcnow() + timedelta(days=expiry_days)
                
                job = Job(
                    id=job_id,
                    session_id=request.session.get("id", ip_hash) if hasattr(request, "session") else ip_hash,
                    user_quota_id=user_quota.id,
                    status=JobStatus.PENDING,
                    original_filename=image_file.filename,
                    original_size=file_size,
                    quality_setting=quality,
                    created_at=datetime.utcnow(),
                    expiry_date=expiry_date
                )
                
                db_session.add(job)
                db_session.flush()
                
                # Enqueue job
                job_queue.enqueue_job(job_id)
                
                # Store file data temporarily (in production, use external storage)
                # For now, we'll store in Redis
                if job_queue.redis_client:
                    job_queue.redis_client.setex(
                        f"file:{job_id}",
                        86400,  # 24 hour TTL
                        file_data
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
            expiry_date=job.expiry_date
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
        event_name = data.get("event_name")
        event_data = data.get("data", {})
        
        # Handle order:created event (successful payment)
        if event_name == "order:created":
            customer_email = event_data.get("customer_email")
            
            # In production, you'd link email to user_quota via authentication
            # For now, we'll create a pro tier entry
            # This would typically be: find user by email, then activate pro
            
            print(f"Payment received for {customer_email}")
        
        return {"success": True, "message": "Webhook processed"}
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Webhook processing error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing failed: {str(e)}"
        )
