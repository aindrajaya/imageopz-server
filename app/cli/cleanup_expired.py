"""
Delete expired outputs once: `python -m app.cli.cleanup_expired` (manual runs; scheduled runs use
POST /internal/maintenance/cleanup on the worker). Idempotent.
Exit 1 when an R2 delete failed or the database is unreachable.
"""

import logging

from app.config import settings
from app.models import db
from app.services import maintenance
from app.services.image_service import r2_storage

logger = logging.getLogger("imageopz.cleanup")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO if not settings.debug else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    session = db.get_session()
    try:
        counts = maintenance.cleanup(session, r2_storage)
    except Exception:
        logger.exception("Cleanup failed")
        return 1
    finally:
        session.close()
    print(f"expired outputs deleted: {counts.deleted}; failed: {counts.failed}")
    return 1 if counts.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
