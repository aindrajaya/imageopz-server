import base64
from datetime import datetime, timedelta


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6q5p0AAAAASUVORK5CYII="
)


def test_pro_credit_cap_blocks_after_limit(client, monkeypatch):
    from app.workers.job_worker import job_queue

    monkeypatch.setattr(job_queue, "enqueue_job", lambda job_id: True)

    from app.models import db, UsersQuota
    from app.config import settings

    s = db.get_session()
    try:
        s.add(
            UsersQuota(
                id="quota_pro_cap",
                session_id="pro_cap_session",
                ip_hash="ip_hash_cap",
                is_pro=True,
                pro_expiry_date=datetime.utcnow() + timedelta(days=10),
                pro_credits_used=max(0, settings.pro_tier_max_credits - 1),
            )
        )
        s.commit()
    finally:
        s.close()

    # 1 image should pass (remaining 1)
    res1 = client.post(
        "/api/v1/optimize",
        files=[("images", ("tiny.png", _PNG_1X1, "image/png"))],
        headers={"X-Session-Id": "pro_cap_session"},
    )
    assert res1.status_code == 202, res1.text

    # Next image should be blocked (remaining 0)
    res2 = client.post(
        "/api/v1/optimize",
        files=[("images", ("tiny2.png", _PNG_1X1, "image/png"))],
        headers={"X-Session-Id": "pro_cap_session"},
    )
    assert res2.status_code == 429, res2.text
    assert "Pro credits exceeded" in res2.text or "Remaining credits" in res2.text

