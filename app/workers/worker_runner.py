"""
Background worker runner for processing queued optimization jobs.
Runs with strict concurrency=1 (single worker loop) to fit 1GB VPS constraints.
"""

import threading
import time
import gc

from app.config import settings
from app.models import db
from app.workers.job_worker import job_queue, JobWorker


class BackgroundWorkerRunner:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self):
        if not settings.worker_enabled:
            return
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="imageopz-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout_seconds: int = 10):
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=timeout_seconds)

    def _run_loop(self):
        while not self._stop_event.is_set():
            if not job_queue.redis_client:
                time.sleep(2)
                continue

            job_id = job_queue.dequeue_job_blocking(timeout_seconds=settings.worker_poll_timeout_seconds)
            if not job_id:
                continue

            db_session = db.get_session()
            try:
                worker = JobWorker(db_session, queue=job_queue)
                worker.process_job(job_id)
            finally:
                try:
                    db_session.close()
                except Exception:
                    pass
                gc.collect()


background_worker = BackgroundWorkerRunner()

