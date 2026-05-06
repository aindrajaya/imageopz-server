"""
Configuration management for ImageOpz backend service.
Loads settings from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from typing import Optional, Literal
from pydantic import field_validator, model_validator


class Settings(BaseSettings):
    # FastAPI
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 1
    proxy_headers: bool = True

    # CORS
    # Comma-separated origins, e.g. "https://imageopz.com,https://www.imageopz.com"
    cors_allow_origins: str = "http://localhost:3000"
    cors_allow_credentials: bool = True

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
    lemonsqueezy_store_id: Optional[str] = None
    lemonsqueezy_variant_id: Optional[str] = None
    lemonsqueezy_redirect_url: Optional[str] = None
    lemonsqueezy_test_mode: bool = False
    checkout_intent_ttl_minutes: int = 15
    activation_pending_retry_ms: int = 1500

    # Supabase Auth (optional)
    supabase_jwks_url: Optional[str] = None
    supabase_jwt_issuer: Optional[str] = None
    supabase_jwt_audience: Optional[str] = None
    supabase_jwks_cache_ttl_seconds: int = 3600
    # Optional fallback verification via Supabase Auth server (needed for HS256 legacy signing).
    # Example: https://<project-ref>.supabase.co/auth/v1
    supabase_auth_url: Optional[str] = None
    # Safe to use publishable/anon key here; required by /auth/v1/user endpoint.
    supabase_anon_key: Optional[str] = None

    # Resend (Email notifications)
    resend_api_key: Optional[str] = None
    resend_from_email: str = "ImageOpz <noreply@imageopz.com>"

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
    pro_tier_max_credits: int = 100

    # File Expiry
    free_tier_expiry_days: int = 7
    pro_tier_expiry_days: int = 40

    # Cleanup
    cleanup_cron_schedule: str = "0 * * * *"  # Hourly at minute 0
    timezone: str = "Asia/Jakarta"

    # Upload temp storage
    upload_dir: str = "./tmp/uploads"

    # Background worker
    worker_enabled: bool = True
    worker_poll_timeout_seconds: int = 5

    # Session cookie (browser id used for quota + payment relinking)
    session_cookie_name: str = "imageopz_sid"
    session_cookie_max_age_days: int = 365

    # Environment
    environment: Literal["development", "staging", "production"] = "development"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"

    @field_validator("debug", mode="before")
    @classmethod
    def _parse_debug(cls, value):
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "yes", "y", "on", "debug", "development", "dev"}:
                return True
            if v in {"0", "false", "no", "n", "off", "release", "production", "prod"}:
                return False
        return value

    def cors_allow_origins_list(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    @model_validator(mode="after")
    def _validate_production_settings(self):
        if self.environment == "production":
            if self.debug:
                raise ValueError("DEBUG must be false in production")
            if not self.secret_key or self.secret_key.startswith("dev-secret-key"):
                raise ValueError("SECRET_KEY must be set to a strong value in production")
            if "*" in self.cors_allow_origins_list():
                raise ValueError("CORS_ALLOW_ORIGINS must not include '*' in production")
            if self.cors_allow_credentials and "*" in self.cors_allow_origins_list():
                raise ValueError("CORS_ALLOW_ORIGINS cannot be '*' when credentials are enabled")
        return self


# Global settings instance
settings = Settings()
