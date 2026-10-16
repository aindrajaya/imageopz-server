"""
Sweep once: `python -m app.cli.sweep_uploads` (scheduling is T-008). Rejects expired presigned uploads, re-enqueues
stale PENDING jobs and fails stuck PROCESSING jobs (app/services/upload_sweeper.py). Idempotent.
Exit 1 when a re-enqueue failed (enqueuer misconfigured or unavailable) or the database is unreachable.
"""

import logging

from app.config import settings
from app.models import db
from app.services import job_enqueuer, upload_store
from app.services.upload_sweeper import expire_stale_uploads, requeue_stale_jobs

logger = logging.getLogger("imageopz.uploads.sweeper")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO if not settings.debug else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        store = upload_store.get_upload_store()
    except upload_store.UploadStoreNotConfigured:
        # Credits are still released; objects are left to the bucket lifecycle rule.
        logger.warning("Uploads bucket not configured; objects will not be deleted")
        store = None
    session = db.get_session()
    try:
        print(f"expired uploads rejected: {expire_stale_uploads(session, store)}")
        requeued, enqueue_failures, failed = requeue_stale_jobs(session, job_enqueuer.get_enqueuer(), store)
        print(f"stale jobs re-enqueued: {requeued} (enqueue failures: {enqueue_failures}); jobs failed: {failed}")
    except Exception:
        logger.exception("Sweep failed")
        return 1
    finally:
        session.close()
    return 1 if enqueue_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
