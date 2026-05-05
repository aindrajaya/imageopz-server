import base64
from datetime import datetime, timedelta


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6q5p0AAAAASUVORK5CYII="
)


def test_pro_quota_allows_more_than_3_without_auth_token(client, monkeypatch):
    # Avoid depending on a running Redis instance in tests.
    from app.workers.job_worker import job_queue

    monkeypatch.setattr(job_queue, "enqueue_job", lambda job_id: True)

    # Seed a pro quota for this session_id (as if webhook activated it)
    from app.models import db, UsersQuota

    s = db.get_session()
    try:
        s.add(
            UsersQuota(
                id="quota_pro_1",
                session_id="pro_session_1",
                ip_hash="ip_hash_1",
                is_pro=True,
                pro_expiry_date=datetime.utcnow() + timedelta(days=10),
            )
        )
        s.commit()
    finally:
        s.close()

    files = [("images", (f"tiny{i}.png", _PNG_1X1, "image/png")) for i in range(4)]
    res = client.post("/api/v1/optimize", files=files, headers={"X-Session-Id": "pro_session_1"})
    assert res.status_code == 202, res.text
