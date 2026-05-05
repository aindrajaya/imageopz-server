import base64


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6q5p0AAAAASUVORK5CYII="
)


def test_optimize_stores_session_id_on_users_quota(client, monkeypatch):
    # Avoid depending on a running Redis instance in tests.
    from app.workers.job_worker import job_queue

    monkeypatch.setattr(job_queue, "enqueue_job", lambda job_id: True)

    res = client.post(
        "/api/v1/optimize",
        files=[("images", ("tiny.png", _PNG_1X1, "image/png"))],
        headers={"X-Session-Id": "session_test_123"},
    )
    assert res.status_code == 202, res.text

    # Verify quota record is keyed by session_id (not only ip_hash)
    from app.models import db, UsersQuota

    s = db.get_session()
    try:
        quota = s.query(UsersQuota).filter_by(session_id="session_test_123").first()
        assert quota is not None
        assert quota.ip_hash is not None
        assert quota.is_pro is False
    finally:
        s.close()

