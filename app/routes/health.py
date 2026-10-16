"""
Health check and utility API routes.
"""

import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models import get_db
from app.schemas import HealthCheckResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger("imageopz.routes.health")


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    responses={200: {"description": "Service is alive"}}
)
async def health_check():
    """
    Liveness: the process answers. No dependency checks and no error text (H1); readiness is /readyz.
    """
    return HealthCheckResponse(status="healthy", version="1.0.0")


@router.get("/readyz", include_in_schema=False)
async def readiness_check(db_session: Session = Depends(get_db)):
    """Readiness: 200 when the database answers, else 503. Generic bodies only; the error is logged."""
    try:
        db_session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "unavailable"})
    return {"status": "ready"}


@router.get("/version")
async def get_version():
    """Get API version"""
    return {"version": "1.0.0", "name": "ImageOpz Backend"}
