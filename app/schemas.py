"""
Pydantic schemas for API request/response validation.
Ensures type safety and automatic OpenAPI documentation.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class JobStatusEnum(str, Enum):
    UPLOADING = "uploading"
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"


# ============= Response Schemas =============

class JobStatusResponse(BaseModel):
    """Job status polling response"""
    job_id: str = Field(..., description="Unique job identifier")
    status: JobStatusEnum = Field(..., description="Current processing status")
    
    # Original file info
    original_filename: str = Field(..., description="Original filename")
    original_size: int = Field(..., description="Original file size in bytes")
    original_width: Optional[int] = Field(None, description="Original image width in pixels")
    original_height: Optional[int] = Field(None, description="Original image height in pixels")
    
    # Optimization results
    optimized_size: Optional[int] = Field(None, description="Optimized file size in bytes")
    compression_ratio: Optional[float] = Field(
        None,
        description="Compression ratio (0.0-1.0, lower is better)"
    )
    
    # Download info (available when status is 'done')
    r2_url: Optional[str] = Field(None, description="Cloudflare R2 download URL")

    # Optional realtime progress (when Redis available)
    progress: Optional[float] = Field(None, description="Progress percentage (0-100)")
    progress_status: Optional[str] = Field(None, description="Progress status message")
    
    # Error handling
    error_message: Optional[str] = Field(None, description="Error details if status is 'failed'")
    rejection_reason: Optional[str] = Field(None, description="Why the upload was refused if status is 'rejected'")
    
    # Metadata
    created_at: datetime = Field(..., description="Job creation timestamp")
    expiry_date: datetime = Field(..., description="File expiration timestamp")
    processing_started_at: Optional[datetime] = Field(None, description="When processing started (UTC)")
    processing_finished_at: Optional[datetime] = Field(None, description="When processing finished (UTC)")
    processing_duration_ms: Optional[int] = Field(None, description="Total processing time in milliseconds")
    
    class Config:
        schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "done",
                "original_filename": "photo.jpg",
                "original_size": 5242880,
                "original_width": 1920,
                "original_height": 1080,
                "optimized_size": 2097152,
                "compression_ratio": 0.4,
                "r2_url": "https://cdn.example.com/optimized/550e8400-e29b-41d4-a716-446655440000.avif",
                "error_message": None,
                "created_at": "2024-01-15T10:30:00Z",
                "expiry_date": "2024-01-22T10:30:00Z",
                "processing_started_at": "2024-01-15T10:30:01Z",
                "processing_finished_at": "2024-01-15T10:30:02Z",
                "processing_duration_ms": 950
            }
        }


class ErrorResponse(BaseModel):
    """Standard error response"""
    success: bool = False
    error: str = Field(..., description="Error type/code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[dict] = Field(None, description="Additional error details")
    
    class Config:
        schema_extra = {
            "example": {
                "success": False,
                "error": "QUOTA_EXCEEDED",
                "message": "Free tier quota exceeded. Maximum 3 credits allowed.",
                "details": {
                    "used": 3,
                    "limit": 3
                }
            }
        }


# ============= Presigned Upload Schemas =============

class PresignFile(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Original filename (stored in the DB only)")
    size: int = Field(..., gt=0, description="Exact file size in bytes")
    content_type: str = Field(..., max_length=50, description="image/jpeg, image/png or image/webp")


class PresignRequest(BaseModel):
    files: List[PresignFile] = Field(..., min_length=1, description="Files to upload")
    auth_token: Optional[str] = Field(None, description="LEGACY Pro JWT from the webhook response (optional)")


class PresignedUpload(BaseModel):
    job_id: str
    object_key: str
    upload_url: str = Field(..., description="Presigned PUT URL (R2 S3 endpoint)")
    method: str = "PUT"
    headers: dict = Field(..., description="Headers the PUT must send exactly (Content-Type)")
    expires_at: datetime = Field(..., description="When upload_url stops working (UTC)")


class PresignResponse(BaseModel):
    files: List[PresignedUpload]
    complete_by: datetime = Field(..., description="Call /uploads/complete before this (UTC)")


class CompleteRequest(BaseModel):
    job_ids: List[str] = Field(..., min_length=1, max_length=100, description="Jobs from /uploads/presign")


class CompletedJob(BaseModel):
    job_id: str
    status: JobStatusEnum
    reason: Optional[str] = Field(None, description="Rejection reason if status is 'rejected'")


class CompleteResponse(BaseModel):
    jobs: List[CompletedJob]


# ============= Authentication Schemas =============

class TokenResponse(BaseModel):
    """JWT token response for Pro tier activation"""
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration in seconds")


class LemonSqueezyWebhookPayload(BaseModel):
    """LemonSqueezy webhook payload for payment events"""
    event_name: str = Field(..., description="Event type (e.g., order:created)")
    data: dict = Field(..., description="Event data")
    meta: dict = Field(..., description="Event metadata")


# ============= Payment Schemas =============

class LemonSqueezyCreateCheckoutRequest(BaseModel):
    """
    Create a LemonSqueezy checkout URL server-side.
    `session_id` should be a stable browser identifier (e.g. UUIDv4 stored in localStorage/cookie).
    """

    email: str = Field(..., description="Customer email to prefill + link purchase")
    session_id: Optional[str] = Field(
        None,
        max_length=255,
        description="Stable browser session identifier (stored in users_quota.session_id)",
    )
    store_id: Optional[str] = Field(
        None,
        description="LemonSqueezy store id (defaults to env LEMONSQUEEZY_STORE_ID)",
    )
    variant_id: Optional[str] = Field(
        None,
        description="LemonSqueezy variant id (defaults to env LEMONSQUEEZY_VARIANT_ID)",
    )
    redirect_url: Optional[str] = Field(
        None,
        description="Override redirect URL after payment (defaults to env LEMONSQUEEZY_REDIRECT_URL)",
    )
    test_mode: Optional[bool] = Field(
        None,
        description="Whether to create this checkout in test mode (defaults to env LEMONSQUEEZY_TEST_MODE)",
    )


class LemonSqueezyCreateCheckoutResponse(BaseModel):
    success: bool = Field(True, description="Whether checkout was created successfully")
    checkout_id: str = Field(..., description="LemonSqueezy checkout id")
    checkout_url: str = Field(..., description="Hosted checkout URL to redirect the user to")
    ip_hash: str = Field(..., description="Server-calculated IP hash (stored in users_quota.ip_hash)")
    session_id: Optional[str] = Field(None, description="Session id used for matching in webhook")
    provider: str = Field("lemonsqueezy", description="Payment provider identifier")


class SessionRestoreRequest(BaseModel):
    email: str = Field(..., description="Email that was used to purchase Pro")


class SessionRestoreResponse(BaseModel):
    success: bool = Field(True, description="Whether restore succeeded")
    session_id: str = Field(..., description="Session id now bound to Pro")
    pro_expiry_date: datetime = Field(..., description="Pro expiry date")


class SessionMeResponse(BaseModel):
    success: bool = Field(True, description="Whether lookup succeeded")
    session_id: str = Field(..., description="Current session id (cookie/header)")
    ip_hash: str = Field(..., description="Server-side IP hash for this request")
    is_authenticated: bool = Field(..., description="Whether Supabase JWT is present and valid")
    supabase_user_id: Optional[str] = Field(None, description="Supabase user id (JWT sub) when authenticated")
    email: Optional[str] = Field(None, description="User email when known (token or linked user record)")
    is_pro: bool = Field(..., description="Whether this session currently has active Pro")
    pro_expiry_date: Optional[datetime] = Field(None, description="Pro expiry date (if pro)")
    pro_verified: bool = Field(..., description="Whether Pro is verified/linked for this session")
    pro_source: str = Field(
        ...,
        description="How Pro was determined: account|session|webhook_session|restore|ip_fallback|unknown|none",
    )
    needs_restore: bool = Field(..., description="UI hint: Pro detected but not verified for this session")
    credits_used: int = Field(..., description="Credits used for free tier tracking")
    free_tier_max_credits: int = Field(..., description="Configured free tier credit limit")
    credits_remaining: int = Field(..., description="Remaining free credits (0 if exhausted; -1 if pro)")
    pro_tier_max_credits: int = Field(..., description="Configured pro tier credit limit")
    pro_credits_used: int = Field(..., description="Credits used in pro tier window")
    pro_credits_remaining: int = Field(..., description="Remaining pro credits (0 if exhausted; -1 if not pro)")
    activation_pending: bool = Field(
        False,
        description="Guest-only UX hint: checkout was started recently and Pro activation may be pending (wait + retry)",
    )
    activation_retry_after_ms: int = Field(
        0,
        description="When activation_pending=true, recommended retry delay in ms",
    )


class SessionLogoutResponse(BaseModel):
    success: bool = Field(True, description="Whether logout succeeded")
    session_id: str = Field(..., description="New session id after rotation")


# ============= Health Check Schemas =============

class HealthCheckResponse(BaseModel):
    """Health check response"""
    status: str = Field("healthy", description="Service status")
    version: str = Field(..., description="API version")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        schema_extra = {
            "example": {
                "status": "healthy",
                "version": "1.0.0",
                "timestamp": "2024-01-15T10:30:00Z"
            }
        }
