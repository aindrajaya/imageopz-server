"""
Pydantic schemas for API request/response validation.
Ensures type safety and automatic OpenAPI documentation.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


class JobStatusEnum(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


# ============= Request Schemas =============

class OptimizeImageRequest(BaseModel):
    """Single image optimization request"""
    filename: str = Field(..., description="Original filename with extension")
    file_data: bytes = Field(..., description="Binary image data")
    
    class Config:
        schema_extra = {
            "example": {
                "filename": "photo.jpg",
                "file_data": b"binary_image_data"
            }
        }


class BulkOptimizeRequest(BaseModel):
    """Bulk image optimization request with multiple images"""
    images: List[OptimizeImageRequest] = Field(
        ...,
        min_items=1,
        description="Array of images to optimize"
    )
    
    @validator("images")
    def validate_images_count(cls, images):
        if len(images) > 30:
            raise ValueError("Maximum 30 images per request")
        return images


# ============= Response Schemas =============

class OptimizationResultResponse(BaseModel):
    """Result of a single image optimization"""
    job_id: str = Field(..., description="Unique job identifier for polling")
    filename: str = Field(..., description="Original filename")
    status: JobStatusEnum = Field(default="pending", description="Current processing status")
    
    class Config:
        schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "filename": "photo.jpg",
                "status": "pending"
            }
        }


class BulkOptimizeResponse(BaseModel):
    """Response for bulk optimization request"""
    success: bool = Field(True, description="Whether all images were queued")
    results: List[OptimizationResultResponse] = Field(
        ...,
        description="Array of job results for each image"
    )
    message: str = Field(
        description="Informational message about the request"
    )


class JobStatusResponse(BaseModel):
    """Job status polling response"""
    job_id: str = Field(..., description="Unique job identifier")
    status: JobStatusEnum = Field(..., description="Current processing status")
    
    # Original file info
    original_filename: str = Field(..., description="Original filename")
    original_size: int = Field(..., description="Original file size in bytes")
    
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
    
    # Metadata
    created_at: datetime = Field(..., description="Job creation timestamp")
    expiry_date: datetime = Field(..., description="File expiration timestamp")
    
    class Config:
        schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "done",
                "original_filename": "photo.jpg",
                "original_size": 5242880,
                "optimized_size": 2097152,
                "compression_ratio": 0.4,
                "r2_url": "https://cdn.example.com/optimized/550e8400-e29b-41d4-a716-446655440000.avif",
                "error_message": None,
                "created_at": "2024-01-15T10:30:00Z",
                "expiry_date": "2024-01-22T10:30:00Z"
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
