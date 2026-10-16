"""
Health check and utility API routes.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models import get_db
from app.schemas import HealthCheckResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    responses={200: {"description": "Service is healthy"}}
)
async def health_check(db_session: Session = Depends(get_db)):
    """
    Check service health and dependencies.
    Returns status of the API and its database connection.
    """
    try:
        db_session.execute(text("SELECT 1"))
        return HealthCheckResponse(
            status="healthy",
            version="1.0.0"
        )
    except Exception as e:
        return HealthCheckResponse(
            status=f"degraded: {str(e)}",
            version="1.0.0"
        )


@router.get("/version")
async def get_version():
    """Get API version"""
    return {"version": "1.0.0", "name": "ImageOpz Backend"}
