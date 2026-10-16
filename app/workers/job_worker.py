"""
Redis queue management and async job worker.
Uses Redis as message broker for job processing.
"""

import enum
import json
import logging
import os
import redis
import tempfile
import time
from typing import Optional, List
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, update

from app.config import settings
from app.models import Job, JobStatus
from app.services.image_service import ImageOptimizer, r2_storage
from app.services import upload_store
from app.services.upload_store import UploadStore
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


# Global instance
job_queue = JobQueue()


class Outcome(str, enum.Enum):
    """Result of one delivery; the internal endpoint maps RETRY to 503 and everything else to 200."""
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"  # not PENDING, or PROCESSING under a live lease, or the claim was lost
    UNKNOWN = "unknown"
    RETRY = "retry"  # transient storage failure; the job is PENDING again


class JobFailed(Exception):
    """Permanent failure: the job becomes FAILED with this message."""


class TransientJobError(Exception):
    """Retriable failure: the claim is released (PROCESSING -> PENDING) and the delivery answers 503."""


def claim_job(db_session, job_id: str, now: datetime, lease_seconds: int) -> bool:
    """PENDING -> PROCESSING, or reclaim a PROCESSING job whose lease expired; one conditional UPDATE, no commit."""
    lease_expired = and_(Job.status == JobStatus.PROCESSING, Job.processing_started_at < now - timedelta(seconds=lease_seconds))
    return db_session.execute(
        update(Job)
        .where(Job.id == job_id, or_(Job.status == JobStatus.PENDING, lease_expired))
        .values(status=JobStatus.PROCESSING, processing_started_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount == 1


class JobWorker:
    """Processes one job per delivery (POST /internal/jobs/process)."""

    def __init__(self, db_session, queue: Optional[JobQueue] = None, store: Optional[UploadStore] = None):
        self.db_session = db_session
        self.queue = queue or job_queue
        self.optimizer = ImageOptimizer()
        self.store = store
        self._source_tmp_path: Optional[str] = None

    def process_job(self, job_id: str) -> bool:
        """True only when this delivery finished the job DONE."""
        return self.run(job_id) == Outcome.DONE

    def run(self, job_id: str) -> Outcome:
        """Process a job; afterwards drop the temp copy and, on DONE/FAILED, the source object in the uploads bucket.
        Database errors propagate (the endpoint answers 503)."""
        self._source_tmp_path = None
        try:
            return self._run(job_id)
        finally:
            self._release_source(job_id)

    def _run(self, job_id: str) -> Outcome:
        # The job row is committed before enqueueing; a short retry still covers a delivery that beats the commit.
        job = None
        for attempt in range(3):
            job = self.db_session.query(Job).filter_by(id=job_id).first()
            if job or attempt == 2:
                break
            time.sleep(0.5)
        if not job:
            logger.warning("Job %s not found; delivery acknowledged", job_id)
            return Outcome.UNKNOWN

        claimed_at = datetime.utcnow()
        claimed = claim_job(self.db_session, job_id, claimed_at, settings.job_lease_seconds)
        self.db_session.commit()
        self.db_session.refresh(job)
        if not claimed:
            # Duplicate delivery, a job still UPLOADING/REJECTED, finished, or under another delivery's lease.
            logger.info("Job %s is %s; not processing", job_id, job.status.name)
            return Outcome.SKIPPED
        t0 = time.perf_counter()
        self.queue.set_job_progress(job_id, 10, "Processing started")

        try:
            values = self._optimize(job)
            status = JobStatus.DONE
        except TransientJobError:
            logger.warning("Job %s: transient failure; released for retry", job_id, exc_info=True)
            released = self._finish(job_id, claimed_at, status=JobStatus.PENDING)
            return Outcome.RETRY if released else Outcome.SKIPPED
        except JobFailed as e:
            values, status = {"processing_error": str(e)}, JobStatus.FAILED
        except Exception as e:
            logger.exception("Unexpected error processing job %s", job_id)
            values, status = {"processing_error": f"Unexpected error: {e}"}, JobStatus.FAILED

        finished = self._finish(
            job_id,
            claimed_at,
            status=status,
            processing_finished_at=datetime.utcnow(),
            processing_duration_ms=int((time.perf_counter() - t0) * 1000),
            **values,
        )
        if not finished:
            logger.warning("Job %s: claim lost (reclaimed or failed by the sweeper); result not written", job_id)
            return Outcome.SKIPPED
        if status == JobStatus.DONE:
            self.queue.set_job_progress(job_id, 100, "Optimization complete")
        return Outcome.DONE if status == JobStatus.DONE else Outcome.FAILED

    def _finish(self, job_id: str, claimed_at: datetime, **values) -> bool:
        """Write the outcome only while this delivery still holds the claim, so DONE is never overwritten."""
        ok = self.db_session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PROCESSING, Job.processing_started_at == claimed_at)
            .values(**values)
            .execution_options(synchronize_session=False)
        ).rowcount == 1
        self.db_session.commit()
        return ok

    def _optimize(self, job: Job) -> dict:
        """Column values for DONE; raises JobFailed (permanent) or TransientJobError (retriable)."""
        self.queue.set_job_progress(job.id, 25, "Stripping metadata")
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
        except TransientJobError:
            raise
        except Exception as e:
            raise JobFailed(f"Metadata stripping failed: {e}")

        self.queue.set_job_progress(job.id, 40, "Optimizing image")
        try:
            optimized_data, optimized_size = self.optimizer.optimize_image(file_data, quality=job.quality_setting)
        except Exception as e:
            raise JobFailed(f"Image optimization failed: {e}")

        self.queue.set_job_progress(job.id, 70, "Uploading to storage")
        # A job is never DONE without an uploaded result (U14); missing configuration is permanent.
        if not r2_storage:
            raise JobFailed("R2 upload failed: outputs storage not configured")
        success, object_key, url_or_error = r2_storage.upload_optimized_image(optimized_data, job.original_filename, job.id)
        if not success:
            raise TransientJobError(f"R2 upload failed: {url_or_error}")

        return {
            "original_width": width,
            "original_height": height,
            "r2_url": url_or_error,
            "r2_object_key": object_key,
            "optimized_size": optimized_size,
            "compression_ratio": optimized_size / job.original_size,
        }

    def _download_source(self, job: Job) -> str:
        """Stream the uploads-bucket object to a temp file; refuses more bytes than were declared at presign.
        A missing object is permanent; any other storage error is transient."""
        store = self.store or upload_store.get_upload_store()
        if store.head(job.source_object_key) is None:
            raise Exception("Source object not found")
        fd, path = tempfile.mkstemp(prefix=f"imageopz-{job.id}-", suffix=".upload")
        self._source_tmp_path = path
        written = 0
        with os.fdopen(fd, "wb") as out:
            try:
                for chunk in store.get_stream(job.source_object_key):
                    written += len(chunk)
                    if job.declared_size_bytes is not None and written > job.declared_size_bytes:
                        raise JobFailed("Source object is larger than the declared size")
                    out.write(chunk)
            except JobFailed:
                raise
            except Exception as e:
                raise TransientJobError("Source download failed") from e
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
                store = self.store or upload_store.get_upload_store()
            except Exception:
                logger.warning("Uploads bucket not configured; leaving %s to the lifecycle rule", job.source_object_key)
                return
            delete_source_best_effort(store, job.source_object_key)
