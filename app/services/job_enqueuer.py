"""
Hands a job to the worker endpoint POST /internal/jobs/process (ADR-005).

JOB_ENQUEUE_MODE:
- "cloudtasks": Cloud Tasks HTTP task with an OIDC token; task name = queue path + job id (+ "-r<n>" for a
  deliberate re-enqueue), so a repeated enqueue of the same attempt is de-duplicated (ALREADY_EXISTS = success).
- "direct" (local dev): synchronous POST with X-Internal-Token (INTERNAL_AUTH_SHARED_SECRET); no queue, no retry.
- unset or unknown: every enqueue fails (fail closed), so /uploads/complete answers 503 instead of losing jobs.

enqueue() returns False on failure and never raises; callers commit the job row first (issues/RACE_CONDITION_FIX.md).
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger("imageopz.job_enqueuer")

PROCESS_PATH = "/internal/jobs/process"
# Maximum Cloud Tasks allows for HTTP tasks ([15 s, 30 min]); see ADR-005 for the doc reference.
DISPATCH_DEADLINE_SECONDS = 1800
DIRECT_TIMEOUT_SECONDS = 300

_tasks_client = None


def task_id(job_id: str, attempt: int = 0) -> str:
    return job_id if attempt == 0 else f"{job_id}-r{attempt}"


class CloudTasksEnqueuer:
    def __init__(self, client, queue: str, target_url: str, sa_email: str, audience: str):
        self.client = client
        self.queue = queue
        self.target_url = target_url.rstrip("/")
        self.sa_email = sa_email
        self.audience = audience

    def build_request(self, job_id: str, attempt: int = 0):
        import json

        from google.cloud import tasks_v2
        from google.protobuf import duration_pb2

        return tasks_v2.CreateTaskRequest(
            parent=self.queue,
            task=tasks_v2.Task(
                name=f"{self.queue}/tasks/{task_id(job_id, attempt)}",
                dispatch_deadline=duration_pb2.Duration(seconds=DISPATCH_DEADLINE_SECONDS),
                http_request=tasks_v2.HttpRequest(
                    url=self.target_url + PROCESS_PATH,
                    http_method=tasks_v2.HttpMethod.POST,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps({"job_id": job_id}).encode(),
                    oidc_token=tasks_v2.OidcToken(service_account_email=self.sa_email, audience=self.audience),
                ),
            ),
        )

    def enqueue(self, job_id: str, attempt: int = 0) -> bool:
        from google.api_core import exceptions

        try:
            self.client.create_task(request=self.build_request(job_id, attempt))
        except exceptions.AlreadyExists:
            return True  # an earlier attempt already queued this task
        except Exception:
            logger.exception("Cloud Tasks enqueue failed for job %s", job_id)
            return False
        return True


class DirectEnqueuer:
    def __init__(self, target_url: str, token: str):
        self.target_url = target_url.rstrip("/")
        self.token = token

    def enqueue(self, job_id: str, attempt: int = 0) -> bool:
        import httpx

        try:
            res = httpx.post(
                self.target_url + PROCESS_PATH,
                json={"job_id": job_id},
                headers={"X-Internal-Token": self.token},
                timeout=DIRECT_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Direct enqueue failed for job %s", job_id)
            return False
        if not 200 <= res.status_code < 300:
            logger.error("Direct enqueue for job %s: worker endpoint returned %d", job_id, res.status_code)
            return False
        return True


class DisabledEnqueuer:
    def __init__(self, reason: str):
        self.reason = reason

    def enqueue(self, job_id: str, attempt: int = 0) -> bool:
        logger.error("Job %s not enqueued: %s", job_id, self.reason)
        return False


def _cloud_tasks_client():
    global _tasks_client
    if _tasks_client is None:
        from google.cloud import tasks_v2

        _tasks_client = tasks_v2.CloudTasksClient()
    return _tasks_client


def get_enqueuer():
    mode = (settings.job_enqueue_mode or "").strip().lower()
    target: Optional[str] = settings.job_tasks_target_url
    if mode == "cloudtasks":
        queue, sa, aud = settings.job_tasks_queue, settings.job_tasks_sa_email, settings.job_tasks_audience
        if not (queue and target and sa and aud):
            return DisabledEnqueuer("JOB_TASKS_QUEUE, JOB_TASKS_TARGET_URL, JOB_TASKS_SA_EMAIL and JOB_TASKS_AUDIENCE are required")
        return CloudTasksEnqueuer(_cloud_tasks_client(), queue, target, sa, aud)
    if mode == "direct":
        if not (target and settings.internal_auth_shared_secret):
            return DisabledEnqueuer("JOB_TASKS_TARGET_URL and INTERNAL_AUTH_SHARED_SECRET are required")
        return DirectEnqueuer(target, settings.internal_auth_shared_secret)
    return DisabledEnqueuer("JOB_ENQUEUE_MODE is unset or unknown")
