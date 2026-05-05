import json
import hmac
import hashlib


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def test_lemonsqueezy_webhook_creates_order_and_user(client, test_env):
    payload_obj = {
        "meta": {"event_name": "order_created", "custom_data": {"ip_hash": "iphash_test_123"}},
        "data": {
            "id": "order_123",
            "attributes": {
                "user_email": "paid@example.com",
            },
        },
    }
    payload = json.dumps(payload_obj)
    signature = _sign(payload, "test-lemonsqueezy-secret")

    res = client.post(
        "/api/v1/webhooks/lemonsqueezy",
        content=payload,
        headers={"X-Signature": signature, "Content-Type": "application/json"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("success") is True
    assert body.get("access_token")

    # Verify persistence
    from app.models import db, User, Order, UsersQuota

    s = db.get_session()
    try:
        user = s.query(User).filter_by(email="paid@example.com").first()
        assert user is not None

        order = s.query(Order).filter_by(provider="lemonsqueezy", external_id="order_123").first()
        assert order is not None
        assert order.user_id == user.id

        quota = s.query(UsersQuota).filter_by(user_id=user.id).first()
        assert quota is not None
        assert quota.is_pro is True
        assert quota.pro_expiry_date is not None
    finally:
        s.close()
