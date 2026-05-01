"""
Configuration management for ImageOpz backend service.
Loads settings from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # FastAPI
    debug: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4

    # Database
    database_url: str = "sqlite:///./imageopz.db"
    database_echo: bool = False

    # Redis & Job Queue
    redis_url: str = "redis://localhost:6379/0"
    redis_password: Optional[str] = None
    queue_name: str = "imageopz_jobs"

    # JWT & Security
    secret_key: str = "dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # Cloudflare R2
    r2_account_id: Optional[str] = None
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket_name: str = "imageopz"
    r2_custom_domain: str = "https://your-domain.r2.dev"

    # LemonSqueezy
    lemonsqueezy_secret: Optional[str] = None
    lemonsqueezy_api_key: Optional[str] = None

    # Sender (Email notifications)
    sender_api_key: Optional[str] = None
    sender_from_email: str = "noreply@imageopz.com"

    # Image Processing
    min_image_size: int = 1 * 1024 * 1024  # 1MB
    free_tier_quality: int = 50
    pro_tier_quality: int = 75
    avif_compression_speed: int = 6
    max_image_pixels: int = 250_000_000  # 250MP

    # Quotas
    free_tier_max_credits: int = 3
    free_tier_max_file_size: int = 10 * 1024 * 1024  # 10MB
    free_tier_max_bulk_images: int = 3
    pro_tier_max_file_size: int = 50 * 1024 * 1024  # 50MB
    pro_tier_max_bulk_images: int = 30

    # File Expiry
    free_tier_expiry_days: int = 7
    pro_tier_expiry_days: int = 40

    # Cleanup
    cleanup_cron_schedule: str = "0 0 * * *"  # Daily at midnight

    # Environment
    environment: str = "development"

    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()
