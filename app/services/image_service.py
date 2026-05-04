"""
Image optimization service using Pillow and AVIF compression.
Integrates with Cloudflare R2 for distributed storage.
"""

import io
import os
import uuid
import logging
from typing import Tuple, Optional
from PIL import Image
import pillow_avif  # noqa - registers AVIF codec
import boto3
from botocore.exceptions import ClientError

from app.config import settings
from app.utils.security import SecurityValidator


logger = logging.getLogger("imageopz.r2")


class ImageOptimizer:
    """Handles image optimization and AVIF conversion"""
    
    @staticmethod
    def optimize_image(
        file_data: bytes,
        quality: int = 50
    ) -> Tuple[bytes, int]:
        """
        Optimize image and convert to AVIF format.
        
        Args:
            file_data: Binary image data
            quality: AVIF quality setting (1-100, higher is better quality)
        
        Returns:
            Tuple of (optimized_bytes, file_size)
        """
        try:
            # Open image
            img = Image.open(io.BytesIO(file_data))
            
            # Convert RGBA to RGB if necessary (AVIF handling)
            if img.mode in ("RGBA", "LA", "P"):
                # Create white background
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")
            
            # Save as AVIF with specified quality
            output = io.BytesIO()
            img.save(
                output,
                format="AVIF",
                quality=quality,
                speed=settings.avif_compression_speed
            )
            
            output.seek(0)
            optimized_data = output.getvalue()
            
            return optimized_data, len(optimized_data)
        
        except Exception as e:
            raise Exception(f"Image optimization failed: {str(e)}")


class R2Storage:
    """Handles uploads and downloads from Cloudflare R2"""
    
    def __init__(self):
        """Initialize S3 client for Cloudflare R2"""
        self.s3_client = None
        self._init_client()
    
    def _init_client(self):
        """Initialize boto3 S3 client for R2"""
        if not all([
            settings.r2_account_id,
            settings.r2_access_key_id,
            settings.r2_secret_access_key
        ]):
            raise ValueError("Cloudflare R2 credentials not configured")
        
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto"
        )
    
    def upload_optimized_image(
        self,
        file_data: bytes,
        original_filename: str,
        job_id: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Upload optimized image to Cloudflare R2.
        
        Args:
            file_data: Binary image data
            original_filename: Original file name for metadata
            job_id: Job ID for unique key generation
        
        Returns:
            Tuple of (success, object_key, public_url)
        """
        try:
            if not self.s3_client:
                self._init_client()
            
            # Generate object key with job ID
            ext = original_filename.split(".")[-1].lower()
            object_key = f"optimized/{job_id}.avif"
            
            # Upload to R2
            self.s3_client.put_object(
                Bucket=settings.r2_bucket_name,
                Key=object_key,
                Body=file_data,
                ContentType="image/avif",
                Metadata={
                    "original-filename": original_filename,
                    "job-id": job_id
                }
            )
            
            # Construct public URL
            public_url = f"{settings.r2_custom_domain}/{object_key}"
            
            return True, object_key, public_url
        
        except ClientError as e:
            error_msg = f"R2 upload failed: {str(e)}"
            return False, None, error_msg
        except Exception as e:
            error_msg = f"Unexpected error uploading to R2: {str(e)}"
            return False, None, error_msg
    
    def delete_optimized_image(self, object_key: str) -> bool:
        """
        Delete image from Cloudflare R2.
        
        Args:
            object_key: R2 object key to delete
        
        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.s3_client:
                self._init_client()
            
            self.s3_client.delete_object(
                Bucket=settings.r2_bucket_name,
                Key=object_key
            )
            return True
        except ClientError as e:
            logger.exception("Failed to delete %s from R2", object_key)
            return False


# Global instance
r2_storage = R2Storage() if all([
    settings.r2_account_id,
    settings.r2_access_key_id,
    settings.r2_secret_access_key
]) else None
