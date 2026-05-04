"""
Security utilities for authentication, validation, and file processing.
Implements EXIF stripping, magic number validation, and quota management.
"""

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Tuple, Optional
from fastapi import HTTPException, status
from PIL import Image
import io

from app.config import settings


class SecurityValidator:
    """Validates files for security threats and integrity"""
    
    ALLOWED_MIME_TYPES = {
        "image/jpeg": [b"\xff\xd8\xff"],
        "image/png": [b"\x89\x50\x4e\x47"],
        "image/webp": [b"\x52\x49\x46\x46"],  # RIFF header
    }
    
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
    
    @staticmethod
    def validate_file_extension(filename: str) -> bool:
        """Verify file has allowed extension"""
        ext = filename.lower().split(".")[-1]
        return f".{ext}" in SecurityValidator.ALLOWED_EXTENSIONS
    
    @staticmethod
    def validate_magic_number(file_data: bytes) -> Tuple[bool, Optional[str]]:
        """
        Validate file magic number to prevent spoofed file types.
        Checks against known JPEG, PNG, and WEBP signatures.
        """
        if len(file_data) < 4:
            return False, "File too small"
        
        for mime_type, signatures in SecurityValidator.ALLOWED_MIME_TYPES.items():
            for sig in signatures:
                if file_data.startswith(sig):
                    return True, mime_type
        
        return False, "Invalid or unsupported image format"

    @staticmethod
    def sniff_mime_type(file_data: bytes) -> Optional[str]:
        """
        Best-effort MIME sniffing using python-magic when available.
        Falls back to signature-based detection.
        """
        try:
            import magic  # type: ignore

            mime = magic.from_buffer(file_data, mime=True)
            return mime
        except Exception:
            valid, mime_or_error = SecurityValidator.validate_magic_number(file_data)
            return mime_or_error if valid else None
    
    @staticmethod
    def strip_exif_and_metadata(file_data: bytes) -> bytes:
        """
        Remove EXIF and all metadata from image to prevent XSS and data leaks.
        Returns clean image data.
        """
        try:
            # Open image and strip all EXIF/metadata
            img = Image.open(io.BytesIO(file_data))
            
            # Create new image without metadata
            data = list(img.getdata())
            image_without_exif = Image.new(img.mode, img.size)
            image_without_exif.putdata(data)
            
            # Save to bytes buffer
            output = io.BytesIO()
            # Determine format from current image
            fmt = img.format or "JPEG"
            image_without_exif.save(output, format=fmt)
            output.seek(0)
            
            return output.getvalue()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to process image: {str(e)}"
            )

    @staticmethod
    def strip_exif_and_metadata_path(file_path: str) -> bytes:
        """
        Remove EXIF/metadata from an image stored on disk.
        """
        try:
            img = Image.open(file_path)
            data = list(img.getdata())
            image_without_exif = Image.new(img.mode, img.size)
            image_without_exif.putdata(data)

            output = io.BytesIO()
            fmt = img.format or "JPEG"
            image_without_exif.save(output, format=fmt)
            output.seek(0)
            return output.getvalue()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to process image: {str(e)}"
            )
    
    @staticmethod
    def validate_decompression_bomb_protection(file_data: bytes) -> bool:
        """
        Validate image won't cause OOM when decompressed.
        Checks total pixel count against MAX_IMAGE_PIXELS limit.
        """
        try:
            img = Image.open(io.BytesIO(file_data))
            total_pixels = img.width * img.height
            
            if total_pixels > settings.max_image_pixels:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Image dimensions too large: {total_pixels:,} pixels exceeds limit of {settings.max_image_pixels:,}"
                )
            
            return True
        except Image.UnidentifiedImageError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot identify image format"
            )

    @staticmethod
    def validate_decompression_bomb_protection_path(file_path: str) -> bool:
        """
        Validate image won't cause OOM when decompressed (path-based).
        """
        try:
            img = Image.open(file_path)
            total_pixels = img.width * img.height

            if total_pixels > settings.max_image_pixels:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Image dimensions too large: {total_pixels:,} pixels exceeds limit of {settings.max_image_pixels:,}"
                )

            return True
        except Image.UnidentifiedImageError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot identify image format"
            )


class QuotaManager:
    """Manages quota tracking and enforcement for free and pro tiers"""
    
    @staticmethod
    def calculate_ip_hash(ip_address: str) -> str:
        """Create consistent hash from IP address"""
        return hashlib.sha256(ip_address.encode()).hexdigest()[:32]
    
    @staticmethod
    def validate_free_tier_quota(
        file_size: int,
        files_count: int,
        credits_used: int,
        credits_requested: int = 1,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate free tier constraints:
        - Max file size: 10MB
        - Max files per request: 3
        - Max total credits: 3
        """
        if file_size > settings.free_tier_max_file_size:
            return False, f"File size {file_size:,} bytes exceeds free tier limit of {settings.free_tier_max_file_size:,} bytes"
        
        if files_count > settings.free_tier_max_bulk_images:
            return False, f"Maximum {settings.free_tier_max_bulk_images} files per request for free tier"
        
        if credits_used + credits_requested > settings.free_tier_max_credits:
            return (
                False,
                f"Free tier quota exceeded. Maximum {settings.free_tier_max_credits} credits allowed"
            )
        
        return True, None
    
    @staticmethod
    def validate_pro_tier_quota(
        file_size: int,
        files_count: int,
        pro_expiry: Optional[datetime]
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate pro tier constraints:
        - Max file size: 50MB
        - Max files per request: 30
        - Pro subscription must be active
        """
        if pro_expiry is None or pro_expiry < datetime.utcnow():
            return False, "Pro subscription expired or not active"
        
        if file_size > settings.pro_tier_max_file_size:
            return False, f"File size {file_size:,} bytes exceeds pro tier limit of {settings.pro_tier_max_file_size:,} bytes"
        
        if files_count > settings.pro_tier_max_bulk_images:
            return False, f"Maximum {settings.pro_tier_max_bulk_images} files per request for pro tier"
        
        return True, None


class JWTHandler:
    """JWT token generation and validation for pro tier"""
    
    @staticmethod
    def generate_token(user_id: str, pro_days: int = None) -> str:
        """Generate JWT token for pro tier access"""
        import jwt

        if pro_days is None:
            pro_days = settings.pro_tier_expiry_days
        
        payload = {
            "sub": user_id,
            "exp": datetime.utcnow() + timedelta(hours=settings.jwt_expiration_hours),
            "iat": datetime.utcnow(),
            "pro_expiry": (datetime.utcnow() + timedelta(days=pro_days)).isoformat()
        }
        
        return jwt.encode(
            payload,
            settings.secret_key,
            algorithm=settings.jwt_algorithm
        )
    
    @staticmethod
    def verify_token(token: str) -> Optional[dict]:
        """Verify and decode JWT token"""
        import jwt
        
        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.jwt_algorithm]
            )
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None


class WebhookValidator:
    """Validates webhook signatures from external services"""
    
    @staticmethod
    def verify_lemonsqueezy_signature(
        payload: str,
        signature: str
    ) -> bool:
        """
        Verify LemonSqueezy webhook signature.
        LemonSqueezy uses HMAC-SHA256.
        """
        if not settings.lemonsqueezy_secret:
            return False
        
        expected_signature = hmac.new(
            settings.lemonsqueezy_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_signature)


class IpFingerprint:
    """Handles IP-based user identification for quota tracking"""
    
    @staticmethod
    def get_ip_hash(request) -> str:
        """Extract IP from request and create hash"""
        # Check for X-Forwarded-For header (proxy/load balancer)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "127.0.0.1"
        
        return QuotaManager.calculate_ip_hash(ip)
