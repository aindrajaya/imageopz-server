"""
Scheduler for cleanup jobs like removing expired files from R2.
Uses APScheduler for cron-based task scheduling.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from sqlalchemy.orm import Session

from app.models import Job, db
from app.services.image_service import r2_storage
from app.config import settings


class CleanupScheduler:
    """Manages scheduled cleanup tasks"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self._setup_jobs()
    
    def _setup_jobs(self):
        """Register cleanup jobs"""
        # Parse cron schedule from config
        cron_parts = settings.cleanup_cron_schedule.split()
        
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
            print("Cleanup scheduler started")
    
    def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            print("Cleanup scheduler stopped")
    
    @staticmethod
    def cleanup_expired_files():
        """
        Remove expired files from R2 and database.
        Runs on schedule to free up storage.
        """
        try:
            db_session = db.get_session()
            
            # Find all expired jobs that haven't been deleted
            expired_jobs = db_session.query(Job).filter(
                Job.expiry_date <= datetime.utcnow(),
                Job.deleted_at == None,
                Job.status == "done"
            ).all()
            
            deleted_count = 0
            failed_count = 0
            
            for job in expired_jobs:
                try:
                    # Delete from R2
                    if job.r2_object_key and r2_storage:
                        if not r2_storage.delete_optimized_image(job.r2_object_key):
                            print(f"Warning: Failed to delete {job.r2_object_key} from R2")
                            failed_count += 1
                            continue
                    
                    # Mark as deleted in database
                    job.deleted_at = datetime.utcnow()
                    job.r2_url = None  # Clear URL
                    db_session.add(job)
                    deleted_count += 1
                
                except Exception as e:
                    print(f"Error cleaning up job {job.id}: {str(e)}")
                    failed_count += 1
            
            db_session.commit()
            db_session.close()
            
            print(f"Cleanup completed: {deleted_count} deleted, {failed_count} failed")
        
        except Exception as e:
            print(f"Critical error in cleanup job: {str(e)}")


# Global scheduler instance
cleanup_scheduler = CleanupScheduler()
