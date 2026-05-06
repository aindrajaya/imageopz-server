import json
from datetime import datetime, timedelta, timezone

import jwt


def _make_token(*, sub: str, email: str, private_key, issuer: str, audience: str) -> str:
    now = datetime.now(tz=timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "email": email,
            "iss": issuer,
            "aud": audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )


def _rsa_jwk_and_private_key():
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    jwk_json = jwt.algorithms.RSAAlgorithm.to_jwk(public_key)
    jwk = json.loads(jwk_json)
    jwk["kid"] = "test-kid"
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk, private_key


def test_session_me_authenticated_does_not_fallback_to_session_pro(client, monkeypatch):
    # Patch JWKS fetch to avoid outbound network.
    from app.utils import supabase_auth
    from app.config import settings

    jwk, private_key = _rsa_jwk_and_private_key()

    async def _fake_get_jwks():
        return {"keys": [jwk]}

    monkeypatch.setattr(supabase_auth._jwks_cache, "get_jwks", _fake_get_jwks)

    token = _make_token(
        sub="sb_user_abc",
        email="sb_user_abc@example.com",
        private_key=private_key,
        issuer=settings.supabase_jwt_issuer,
        audience=settings.supabase_jwt_audience or "authenticated",
    )

    # Seed a session-based pro quota (guest) that should NOT affect authenticated response.
    from app.models import db, UsersQuota

    s = db.get_session()
    try:
        s.add(
            UsersQuota(
                id="quota_session_pro",
                session_id="sess_pro_1",
                ip_hash="ip_hash_1",
                is_pro=True,
                pro_expiry_date=datetime.utcnow() + timedelta(days=10),
                pro_verified=True,
                pro_source="webhook",
            )
        )
        s.commit()
    finally:
        s.close()

    res = client.get(
        "/api/v1/session/me",
        headers={"Authorization": f"Bearer {token}", "X-Session-Id": "sess_pro_1"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_authenticated"] is True
    assert body["is_pro"] is False
    assert body["needs_restore"] is True


def test_restore_authenticated_binds_pro_to_account(client, monkeypatch):
    from app.utils import supabase_auth
    from app.config import settings

    jwk, private_key = _rsa_jwk_and_private_key()

    async def _fake_get_jwks():
        return {"keys": [jwk]}

    monkeypatch.setattr(supabase_auth._jwks_cache, "get_jwks", _fake_get_jwks)

    token = _make_token(
        sub="sb_user_paid",
        email="paid@example.com",
        private_key=private_key,
        issuer=settings.supabase_jwt_issuer,
        audience=settings.supabase_jwt_audience or "authenticated",
    )

    # Seed an existing paid user + pro quota as if created by webhook.
    from app.models import db, User, UsersQuota

    s = db.get_session()
    try:
        user = User(email="paid@example.com")
        s.add(user)
        s.flush()
        s.add(
            UsersQuota(
                id="quota_paid_user",
                user_id=user.id,
                is_pro=True,
                pro_expiry_date=datetime.utcnow() + timedelta(days=10),
                pro_verified=True,
                pro_source="webhook",
            )
        )
        s.commit()
    finally:
        s.close()

    # Claim via restore
    res_restore = client.post(
        "/api/v1/session/restore",
        json={"email": "paid@example.com"},
        headers={"Authorization": f"Bearer {token}", "X-Session-Id": "sess_x"},
    )
    assert res_restore.status_code == 200, res_restore.text

    # Now authenticated session/me should show pro without relying on session/ip.
    res_me = client.get("/api/v1/session/me", headers={"Authorization": f"Bearer {token}", "X-Session-Id": "sess_x"})
    assert res_me.status_code == 200, res_me.text
    body = res_me.json()
    assert body["is_authenticated"] is True
    assert body["is_pro"] is True
    assert body["pro_verified"] is True
    assert body["needs_restore"] is False

