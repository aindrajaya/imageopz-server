"""
Redis queue management and async job worker.
Uses Redis as message broker for job processing.
"""

import json
import logging
import os
import redis
import tempfile
import time
from typing import Optional, List
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import update

from app.config import settings
from app.models import Job, JobStatus, UsersQuota
from app.services.image_service import ImageOptimizer, r2_storage
from app.services.upload_store import UploadStore, get_upload_store
from app.services.uploads import delete_source_best_effort
from app.utils.security import SecurityValidator


logger = logging.getLogger("imageopz.queue")


class JobQueue:
    """Manages job queue in Redis"""
    
    def __init__(self):
        self.redis_client = None
        self._init_redis()
    
    def _init_redis(self):
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.from_url(
                settings.redis_url,
                decode_responses=False,
                socket_connect_timeout=5,
                socket_keepalive=True
            )
            # Test connection
            self.redis_client.ping()
        except redis.ConnectionError:
            logger.warning("Could not connect to Redis at %s", settings.redis_url)
    
    def enqueue_job(self, job_id: str) -> bool:
        """
        Add job ID to processing queue.
        
        Args:
            job_id: Job UUID to enqueue
        
        Returns:
            True if enqueued successfully
        """
        if not self.redis_client:
            return False
        
        try:
            self.redis_client.rpush(settings.queue_name, job_id)
            return True
        except Exception as e:
            logger.exception("Failed to enqueue job %s", job_id)
            return False
    
    def dequeue_job(self) -> Optional[str]:
        """
        Get next job from queue for processing.
        
        Returns:
            Job ID or None if queue empty
        """
        if not self.redis_client:
            return None
        
        try:
            job_id = self.redis_client.lpop(settings.queue_name)
            return job_id.decode("utf-8") if job_id else None
        except Exception as e:
            logger.exception("Failed to dequeue job")
            return None

    def dequeue_job_blocking(self, timeout_seconds: int = 5) -> Optional[str]:
        """
        Blocking pop to avoid busy looping.

        Returns:
            Job ID or None on timeout/error.
        """
        if not self.redis_client:
            return None

        try:
            res = self.redis_client.blpop(settings.queue_name, timeout=timeout_seconds)
            if not res:
                return None
            _, job_id = res
            return job_id.decode("utf-8") if job_id else None
        except Exception as e:
            logger.exception("Failed to dequeue job (blocking)")
            return None
    
    def queue_length(self) -> int:
        """Get current queue size"""
        if not self.redis_client:
            return 0
        
        try:
            return self.redis_client.llen(settings.queue_name)
        except Exception as e:
            logger.exception("Failed to get queue length")
            return 0
    
    def set_job_progress(self, job_id: str, progress: float, status: str = None):
        """
        Store job progress in Redis for real-time updates.
        
        Args:
            job_id: Job ID
            progress: Progress percentage (0-100)
            status: Optional status message
        """
        if not self.redis_client:
            return
        
        try:
            key = f"job:{job_id}:progress"
            data = {
                "progress": min(100, max(0, progress)),
                "timestamp": datetime.utcnow().isoformat()
            }
            if status:
                data["status"] = status
            
            self.redis_client.setex(
                key,
                86400,  # 24 hour TTL
                json.dumps(data).encode("utf-8")
            )
        except Exception as e:
            logger.exception("Failed to set job progress for %s", job_id)
    
    def get_job_progress(self, job_id: str) -> Optional[dict]:
        """
        Retrieve job progress from Redis.
        
        Returns:
            Progress data dict or None
        """
        if not self.redis_client:
            return None
        
        try:
            key = f"job:{job_id}:progress"
            data = self.redis_client.get(key)
            if not data:
                return None
            return json.loads(data.decode("utf-8"))
        except Exception as e:
            logger.exception("Failed to get job progress for %s", job_id)
            return None


class JobWorker:
    """Processes image optimization jobs from queue"""
    
    def __init__(self, db_session, queue: Optional[JobQueue] = None, store: Optional[UploadStore] = None):
        self.db_session = db_session
        self.queue = queue or JobQueue()
        self.optimizer = ImageOptimizer()
        self.store = store
        self._source_tmp_path: Optional[str] = None

    def process_job(self, job_id: str) -> bool:
        """Process a job; afterwards drop the temp copy and, on DONE/FAILED, the source object in the uploads bucket."""
        self._source_tmp_path = None
        try:
            return self._process_job(job_id)
        finally:
            self._release_source(job_id)

    def _process_job(self, job_id: str) -> bool:
        """
        Process a single job: optimize image and upload to R2.
        
        Args:
            job_id: Job ID to process
        
        Returns:
            True if successful, False on error
        """
        try:
            # Retrieve job from database with retry logic
            # Retry up to 3 times with 500ms delay to handle race condition with API
            # where Redis is faster than SQLite commit
            job = None
            max_retries = 3
            retry_delay = 0.5  # seconds
            
            for attempt in range(max_retries):
                job = self.db_session.query(Job).filter_by(id=job_id).first()
                if job:
                    break
                
                if attempt < max_retries - 1:
                    logger.warning(
                        "Job %s not found in database (attempt %d/%d), retrying in %.1fs...",
                        job_id, attempt + 1, max_retries, retry_delay
                    )
                    time.sleep(retry_delay)
            
            if not job:
                logger.warning(
                    "Job %s not found in database after %d retries. This may indicate "
                    "a critical issue with the database or API persistence.",
                    job_id, max_retries
                )
                return False

            t0 = time.perf_counter()
            started_at = datetime.utcnow()

            # Claim PENDING -> PROCESSING conditionally: a duplicate delivery, or a job still UPLOADING/REJECTED,
            # must not be processed (or overwrite a finished result).
            claimed = self.db_session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.PENDING)
                .values(status=JobStatus.PROCESSING, processing_started_at=started_at)
                .execution_options(synchronize_session=False)
            ).rowcount == 1
            self.db_session.commit()
            self.db_session.refresh(job)
            if not claimed:
                logger.warning("Job %s is %s, not PENDING; not processing", job_id, job.status.name)
                return False
            self.queue.set_job_progress(job_id, 10, "Processing started")
            
            self.queue.set_job_progress(job_id, 25, "Stripping metadata")
            
            # Strip EXIF and metadata
            try:
                if not job.source_object_key:
                    raise Exception("Original file not found")
                # Final guard (D3): the bytes may have been replaced after /complete via the still-valid URL.
                source_path = self._download_source(job)
                formats = SecurityValidator.pil_formats(job.declared_content_type)
                with open(source_path, "rb") as fh:
                    valid, magic_mime = SecurityValidator.validate_magic_number(fh.read(64 * 1024))
                if not formats or not valid or magic_mime != job.declared_content_type:
                    raise Exception("Source content does not match the declared image type")
                SecurityValidator.validate_decompression_bomb_protection_path(source_path, formats)
                file_data, width, height = SecurityValidator.strip_exif_and_metadata_path_with_dimensions(source_path, formats)

                job.original_width = width
                job.original_height = height
            except Exception as e:
                job.status = JobStatus.FAILED
                job.processing_error = f"Metadata stripping failed: {str(e)}"
                job.processing_finished_at = datetime.utcnow()
                job.processing_duration_ms = int((time.perf_counter() - t0) * 1000)
                self.db_session.commit()
                return False
            
            self.queue.set_job_progress(job_id, 40, "Optimizing image")
            
            # Optimize image
            try:
                optimized_data, optimized_size = self.optimizer.optimize_image(
                    file_data,
                    quality=job.quality_setting
                )
            except Exception as e:
                job.status = JobStatus.FAILED
                job.processing_error = f"Image optimization failed: {str(e)}"
                job.processing_finished_at = datetime.utcnow()
                job.processing_duration_ms = int((time.perf_counter() - t0) * 1000)
                self.db_session.commit()
                return False
            
            self.queue.set_job_progress(job_id, 70, "Uploading to storage")
            
            # Upload to R2; a job is never DONE without an uploaded result (U14)
            if r2_storage:
                success, object_key, url_or_error = r2_storage.upload_optimized_image(
                    optimized_data,
                    job.original_filename,
                    job_id
                )
            else:
                success, object_key, url_or_error = False, None, "outputs storage not configured"

            if not success:
                job.status = JobStatus.FAILED
                job.processing_error = f"R2 upload failed: {url_or_error}"
                job.processing_finished_at = datetime.utcnow()
                job.processing_duration_ms = int((time.perf_counter() - t0) * 1000)
                self.db_session.commit()
                return False

            job.r2_url = url_or_error
            job.r2_object_key = object_key
            
            # Update job with results
            job.optimized_size = optimized_size
            job.compression_ratio = optimized_size / job.original_size
            job.status = JobStatus.DONE
            job.processing_finished_at = datetime.utcnow()
            job.processing_duration_ms = int((time.perf_counter() - t0) * 1000)
            self.queue.set_job_progress(job_id, 100, "Optimization complete")
            
            self.db_session.commit()

            return True
        
        except Exception as e:
            logger.exception("Unexpected error processing job %s", job_id)
            try:
                job.status = JobStatus.FAILED
                job.processing_error = f"Unexpected error: {str(e)}"
                if "t0" in locals():
                    job.processing_finished_at = datetime.utcnow()
                    job.processing_duration_ms = int((time.perf_counter() - t0) * 1000)
                self.db_session.commit()
            except:
                pass
            return False
    
    def _download_source(self, job: Job) -> str:
        """Stream the uploads-bucket object to a temp file; refuses more bytes than were declared at presign."""
        store = self.store or get_upload_store()
        fd, path = tempfile.mkstemp(prefix=f"imageopz-{job.id}-", suffix=".upload")
        self._source_tmp_path = path
        written = 0
        with os.fdopen(fd, "wb") as out:
            for chunk in store.get_stream(job.source_object_key):
                written += len(chunk)
                if job.declared_size_bytes is not None and written > job.declared_size_bytes:
                    raise Exception("Source object is larger than the declared size")
                out.write(chunk)
        return path

    def _release_source(self, job_id: str) -> None:
        if self._source_tmp_path:
            try:
                os.remove(self._source_tmp_path)
            except OSError:
                pass
            self._source_tmp_path = None
        try:
            job = self.db_session.query(Job).filter_by(id=job_id).first()
        except Exception:
            self.db_session.rollback()
            return
        if job and job.source_object_key and job.status in (JobStatus.DONE, JobStatus.FAILED):
            try:
                store = self.store or get_upload_store()
            except Exception:
                logger.warning("Uploads bucket not configured; leaving %s to the lifecycle rule", job.source_object_key)
                return
            delete_source_best_effort(store, job.source_object_key)

    def process_queue(self, max_jobs: Optional[int] = None):
        """
        Process all jobs in queue (for batch processing).
        
        Args:
            max_jobs: Maximum jobs to process in this run (None = all)
        """
        jobs_processed = 0
        
        while True:
            if max_jobs and jobs_processed >= max_jobs:
                break
            
            job_id = self.queue.dequeue_job()
            if not job_id:
                break
            
            success = self.process_job(job_id)
            jobs_processed += 1
            
            if success:
                print(f"✓ Processed job {job_id}")
            else:
                print(f"✗ Failed to process job {job_id}")


# Global instance
job_queue = JobQueue()
