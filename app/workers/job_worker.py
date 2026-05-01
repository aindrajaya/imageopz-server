"""
Redis queue management and async job worker.
Uses Redis as message broker for job processing.
"""

import json
import redis
from typing import Optional, List
from datetime import datetime, timedelta
from uuid import uuid4

from app.config import settings
from app.models import Job, JobStatus, UsersQuota
from app.services.image_service import ImageOptimizer, r2_storage
from app.utils.security import SecurityValidator


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
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True
            )
            # Test connection
            self.redis_client.ping()
        except redis.ConnectionError:
            print(f"Warning: Could not connect to Redis at {settings.redis_url}")
    
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
            print(f"Failed to enqueue job {job_id}: {str(e)}")
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
            return job_id
        except Exception as e:
            print(f"Failed to dequeue job: {str(e)}")
            return None
    
    def queue_length(self) -> int:
        """Get current queue size"""
        if not self.redis_client:
            return 0
        
        try:
            return self.redis_client.llen(settings.queue_name)
        except Exception as e:
            print(f"Failed to get queue length: {str(e)}")
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
                json.dumps(data)
            )
        except Exception as e:
            print(f"Failed to set job progress for {job_id}: {str(e)}")
    
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
            return json.loads(data) if data else None
        except Exception as e:
            print(f"Failed to get job progress for {job_id}: {str(e)}")
            return None


class JobWorker:
    """Processes image optimization jobs from queue"""
    
    def __init__(self, db_session):
        self.db_session = db_session
        self.queue = JobQueue()
        self.optimizer = ImageOptimizer()
    
    def process_job(self, job_id: str) -> bool:
        """
        Process a single job: optimize image and upload to R2.
        
        Args:
            job_id: Job ID to process
        
        Returns:
            True if successful, False on error
        """
        try:
            # Retrieve job from database
            job = self.db_session.query(Job).filter_by(id=job_id).first()
            if not job:
                print(f"Job {job_id} not found in database")
                return False
            
            # Update status to processing
            job.status = JobStatus.PROCESSING
            self.db_session.commit()
            self.queue.set_job_progress(job_id, 10, "Processing started")
            
            # Retrieve original file (from temp storage or re-fetch from request)
            # This assumes file is stored temporarily or we have metadata to retrieve it
            file_data = self._get_file_data(job_id)
            if not file_data:
                job.status = JobStatus.FAILED
                job.processing_error = "Original file not found"
                self.db_session.commit()
                return False
            
            self.queue.set_job_progress(job_id, 25, "Stripping metadata")
            
            # Strip EXIF and metadata
            try:
                file_data = SecurityValidator.strip_exif_and_metadata(file_data)
            except Exception as e:
                job.status = JobStatus.FAILED
                job.processing_error = f"Metadata stripping failed: {str(e)}"
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
                self.db_session.commit()
                return False
            
            self.queue.set_job_progress(job_id, 70, "Uploading to storage")
            
            # Upload to R2
            if r2_storage:
                success, object_key, url_or_error = r2_storage.upload_optimized_image(
                    optimized_data,
                    job.original_filename,
                    job_id
                )
                
                if not success:
                    job.status = JobStatus.FAILED
                    job.processing_error = f"R2 upload failed: {url_or_error}"
                    self.db_session.commit()
                    return False
                
                job.r2_url = url_or_error
                job.r2_object_key = object_key
            
            # Update job with results
            job.optimized_size = optimized_size
            job.compression_ratio = optimized_size / job.original_size
            job.status = JobStatus.DONE
            self.queue.set_job_progress(job_id, 100, "Optimization complete")
            
            self.db_session.commit()
            
            # Clean up temporary file
            self._cleanup_temp_file(job_id)
            
            return True
        
        except Exception as e:
            print(f"Unexpected error processing job {job_id}: {str(e)}")
            try:
                job.status = JobStatus.FAILED
                job.processing_error = f"Unexpected error: {str(e)}"
                self.db_session.commit()
            except:
                pass
            return False
    
    def _get_file_data(self, job_id: str) -> Optional[bytes]:
        """
        Retrieve original file data for job.
        In a production system, this would retrieve from temp storage or a file server.
        """
        # TODO: Implement based on your file storage strategy
        # Options:
        # 1. Store files in /tmp directory during upload
        # 2. Store in Redis temporarily
        # 3. Retrieve from initial upload request body
        pass
    
    def _cleanup_temp_file(self, job_id: str):
        """Remove temporary file after processing"""
        # TODO: Implement based on storage strategy
        pass
    
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
