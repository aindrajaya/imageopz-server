"""
Health check and utility API routes.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models import db, get_db
from app.schemas import HealthCheckResponse
from app.workers.job_worker import job_queue

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    responses={200: {"description": "Service is healthy"}}
)
async def health_check(db_session: Session = Depends(get_db)):
    """
    Check service health and dependencies.
    Returns status of API, database, and Redis connections.
    """
    try:
        # Check database
        db_session.execute(text("SELECT 1"))
        
        # Check Redis
        redis_status = "disconnected"
        if job_queue.redis_client:
            try:
                job_queue.redis_client.ping()
                redis_status = "connected"
            except Exception:
                redis_status = "error"
        
        return HealthCheckResponse(
            status=f"healthy ({redis_status})",
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
