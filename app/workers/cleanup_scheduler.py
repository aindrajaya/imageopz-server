"""
Scheduler for cleanup jobs like removing expired files from R2.
Uses APScheduler for cron-based task scheduling.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.models import db
from app.services.output_cleanup import cleanup_expired_outputs
from app.services.image_service import r2_storage
from app.config import settings


logger = logging.getLogger("imageopz.cleanup")


class CleanupScheduler:
    """Manages scheduled cleanup tasks"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone=settings.timezone)
        self._setup_jobs()
    
    def _setup_jobs(self):
        """Register cleanup jobs"""
        # Parse cron schedule from config
        cron_parts = (settings.cleanup_cron_schedule or "").split()
        if len(cron_parts) != 5:
            logger.warning(
                "Invalid CLEANUP_CRON_SCHEDULE=%r; expected 5 fields, using hourly default '0 * * * *'",
                settings.cleanup_cron_schedule,
            )
            cron_parts = ["0", "*", "*", "*", "*"]
        
        self.scheduler.add_job(
            func=self.cleanup_expired_files,
            trigger=CronTrigger(
                minute=cron_parts[0],
                hour=cron_parts[1],
                day=cron_parts[2],
                month=cron_parts[3],
                day_of_week=cron_parts[4]
            ),
            id="cleanup_expired_files",
            name="Cleanup Expired Files",
            replace_existing=True
        )
    
    def start(self):
        """Start the scheduler"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Cleanup scheduler started")
    
    def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Cleanup scheduler stopped")
    
    @staticmethod
    def cleanup_expired_files():
        """Remove expired files from R2 and database (app/services/output_cleanup.py)."""
        try:
            db_session = db.get_session()
            try:
                cleanup_expired_outputs(db_session, r2_storage)
            finally:
                db_session.close()
        except Exception:
            logger.exception("Critical error in cleanup job")


# Global scheduler instance
cleanup_scheduler = CleanupScheduler()
