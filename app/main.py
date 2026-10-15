"""
FastAPI application factory and startup/shutdown logic.
Initializes database, scheduler, workers, and API routes.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.exception_handlers import RequestValidationError
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from app.config import settings
from app.models import db
from app.routes import optimization, health, session, internal, uploads
from app.services.upload_store import uploads_configured

# Configure logging
logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifecycle.
    Handles startup and shutdown of background tasks and connections.
    """
    # Startup
    logger.info("Starting ImageOpz Backend Service")
    
    # Initialize database
    db.init_db()
    logger.info("Database initialized")

    if not uploads_configured():
        logger.warning("R2 uploads bucket not configured (R2_UPLOADS_*); /api/v1/uploads/* will return 503")

    # Log key routes (helps diagnose mismatched deployments / stale reloads)
    try:
        key_paths = {"/api/v1/checkout/lemonsqueezy", "/api/v1/session/restore", "/api/v1/webhooks/lemonsqueezy"}
        for route in getattr(app, "routes", []):
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if path in key_paths:
                logger.info("Route registered: %s methods=%s", path, sorted(list(methods)) if methods else None)
    except Exception:
        pass

    logger.info("ImageOpz Backend Service ready")
    
    yield
    
    # Shutdown
    logger.info("Shutting down ImageOpz Backend Service")
    logger.info("ImageOpz Backend Service stopped")


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.
    
    Returns:
        Configured FastAPI instance
    """
    app = FastAPI(
        title="ImageOpz Backend API",
        description="High-performance image optimization service with AVIF support",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins_list(),
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        # Must include Authorization for Supabase JWT authenticated calls.
        # Keep explicit list (not '*') because we may use credentials in dev/prod.
        allow_headers=["Content-Type", "X-Session-Id", "Authorization"],
    )
    
    # Add compression middleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Lightweight request logging for diagnosing frontend integration issues (CORS/auth/session).
    # Never logs bearer tokens or full cookies.
    @app.middleware("http")
    async def _log_key_requests(request, call_next):
        key_paths = {
            "/api/v1/session/me",
            "/api/v1/session/restore",
            "/api/v1/session/reconcile",
            "/api/v1/checkout/lemonsqueezy",
            "/api/v1/optimize",
            "/api/v1/uploads/presign",
            "/api/v1/uploads/complete",
        }
        should_log = request.url.path in key_paths
        if should_log:
            auth = request.headers.get("authorization") or ""
            auth_has_bearer = auth.lower().startswith("bearer ")
            auth_token = auth.split(" ", 1)[1].strip() if auth_has_bearer and " " in auth else ""
            auth_is_jwt_like = auth_has_bearer and (auth_token.count(".") == 2)
            logger.info(
                "HTTP %s %s origin=%s acr_method=%s acr_headers=%s x_session_id=%s cookie_present=%s auth_present=%s auth_jwt_like=%s",
                request.method,
                request.url.path,
                request.headers.get("origin"),
                request.headers.get("access-control-request-method"),
                request.headers.get("access-control-request-headers"),
                request.headers.get("x-session-id"),
                bool(request.headers.get("cookie")),
                bool(auth),
                bool(auth_is_jwt_like),
            )
        resp = await call_next(request)
        if should_log:
            logger.info("HTTP %s %s -> %s", request.method, request.url.path, resp.status_code)
        return resp
    
    # Custom exception handler for validation errors
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": exc.errors()
            }
        )
    
    # Include routes
    app.include_router(health.router)
    app.include_router(optimization.router)
    app.include_router(uploads.router)
    app.include_router(session.router)
    app.include_router(internal.router)
    
    # Root endpoint
    @app.get("/")
    async def root():
        """API root endpoint"""
        return {
            "name": "ImageOpz Backend API",
            "version": "1.0.0",
            "status": "running",
            "docs": "/docs",
            "health": "/health"
        }
    
    return app


# Create application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        workers=settings.api_workers if not settings.debug else 1,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info"
    )
