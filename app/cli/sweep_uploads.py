"""Reject expired presigned uploads once: `python -m app.cli.sweep_uploads` (scheduling is T-007/T-008)."""

import logging

from app.config import settings
from app.models import db
from app.services import upload_store
from app.services.upload_sweeper import expire_stale_uploads


def main() -> int:
    logging.basicConfig(
        level=logging.INFO if not settings.debug else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        store = upload_store.get_upload_store()
    except upload_store.UploadStoreNotConfigured:
        # Credits are still released; objects are left to the bucket lifecycle rule.
        logging.getLogger("imageopz.uploads.sweeper").warning("Uploads bucket not configured; objects will not be deleted")
        store = None
    session = db.get_session()
    try:
        print(f"expired uploads rejected: {expire_stale_uploads(session, store)}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
