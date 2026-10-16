"""
Sweep once: `python -m app.cli.sweep_uploads` (manual runs; scheduled runs use POST /internal/maintenance/sweep on the
worker). Rejects expired presigned uploads, re-enqueues stale PENDING jobs and fails stuck PROCESSING jobs
(app/services/maintenance.py). Idempotent.
Exit 1 when a re-enqueue failed (enqueuer misconfigured or unavailable) or the database is unreachable.
"""

import logging

from app.config import settings
from app.models import db
from app.services import maintenance

logger = logging.getLogger("imageopz.uploads.sweeper")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO if not settings.debug else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    session = db.get_session()
    try:
        counts = maintenance.sweep(session)
    except Exception:
        logger.exception("Sweep failed")
        return 1
    finally:
        session.close()
    print(f"expired uploads rejected: {counts.expired_uploads}")
    print(f"stale jobs re-enqueued: {counts.requeued} (enqueue failures: {counts.enqueue_failures}); jobs failed: {counts.failed}")
    return 1 if counts.enqueue_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
