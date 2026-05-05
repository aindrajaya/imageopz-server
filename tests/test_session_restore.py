from datetime import datetime, timedelta


def test_session_restore_relinks_pro_to_new_session(client):
    from app.models import db, User, UsersQuota

    s = db.get_session()
    try:
        user = User(email="restore@example.com")
        s.add(user)
        s.flush()

        quota = UsersQuota(
            user_id=user.id,
            ip_hash="old_ip_hash",
            session_id="old_session",
            is_pro=True,
            pro_expiry_date=datetime.utcnow() + timedelta(days=10),
        )
        s.add(quota)
        s.commit()
    finally:
        s.close()

    res = client.post(
        "/api/v1/session/restore",
        json={"email": "restore@example.com"},
        headers={"X-Session-Id": "new_session_123"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    assert body["session_id"] == "new_session_123"
    assert body.get("pro_expiry_date")

    s = db.get_session()
    try:
        new_quota = s.query(UsersQuota).filter_by(session_id="new_session_123").first()
        assert new_quota is not None
        assert new_quota.is_pro is True
        assert new_quota.user_id is not None
        assert new_quota.pro_expiry_date is not None
    finally:
        s.close()

