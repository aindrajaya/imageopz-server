"""
Delete expired outputs once: `python -m app.cli.cleanup_expired` (scheduling is T-008). Idempotent.
Exit 1 when an R2 delete failed or the database is unreachable.
"""

import logging

from app.config import settings
from app.models import db
from app.services.image_service import r2_storage
from app.services.output_cleanup import cleanup_expired_outputs

logger = logging.getLogger("imageopz.cleanup")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO if not settings.debug else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    session = db.get_session()
    try:
        deleted, failed = cleanup_expired_outputs(session, r2_storage)
    except Exception:
        logger.exception("Cleanup failed")
        return 1
    finally:
        session.close()
    print(f"expired outputs deleted: {deleted}; failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
