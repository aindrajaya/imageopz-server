#!/usr/bin/env python3
"""
ImageOpz background worker entry point.
Runs the job worker loop and (optionally) the cleanup scheduler in the same process.
"""

import sys
import time
import signal
import argparse
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.models import db
from app.workers.cleanup_scheduler import cleanup_scheduler
from app.workers.worker_runner import background_worker


logger = logging.getLogger("imageopz.worker")


def main() -> int:
    parser = argparse.ArgumentParser(description="ImageOpz Worker Service")
    parser.add_argument(
        "--with-scheduler",
        action="store_true",
        default=True,
        help="Run cleanup scheduler in this process (default: true)",
    )
    parser.add_argument(
        "--no-scheduler",
        action="store_true",
        default=False,
        help="Disable cleanup scheduler (overrides --with-scheduler)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if not settings.debug else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    run_scheduler = bool(args.with_scheduler) and not bool(args.no_scheduler)

    logger.info("Starting ImageOpz worker process")
    db.init_db()
    logger.info("Database initialized")

    if run_scheduler:
        cleanup_scheduler.start()
        logger.info("Cleanup scheduler started")

    background_worker.start()
    logger.info("Background worker started")

    stop_requested = {"value": False}

    def _handle_stop(signum, frame):
        stop_requested["value"] = True

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    try:
        while not stop_requested["value"]:
            time.sleep(0.5)
    finally:
        logger.info("Stopping ImageOpz worker process")
        background_worker.stop()
        if run_scheduler:
            cleanup_scheduler.stop()
        logger.info("Worker process stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

