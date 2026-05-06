import json
from datetime import datetime, timedelta, timezone

import jwt


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


def test_verify_supabase_jwt_valid(monkeypatch):
    from app.utils import supabase_auth

    jwk, private_key = _rsa_jwk_and_private_key()

    async def _fake_get_jwks():
        return {"keys": [jwk]}

    monkeypatch.setattr(supabase_auth._jwks_cache, "get_jwks", _fake_get_jwks)

    now = datetime.now(tz=timezone.utc)
    token = jwt.encode(
        {
            "sub": "sb_user_123",
            "email": "sb@example.com",
            "iss": "https://supabase.test/auth/v1",
            "aud": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )

    ident = __import__("asyncio").get_event_loop().run_until_complete(supabase_auth.verify_supabase_jwt(token))
    assert ident.sub == "sb_user_123"
    assert ident.email == "sb@example.com"


def test_verify_supabase_jwt_invalid_issuer(monkeypatch):
    from app.utils import supabase_auth

    jwk, private_key = _rsa_jwk_and_private_key()

    async def _fake_get_jwks():
        return {"keys": [jwk]}

    monkeypatch.setattr(supabase_auth._jwks_cache, "get_jwks", _fake_get_jwks)

    now = datetime.now(tz=timezone.utc)
    token = jwt.encode(
        {
            "sub": "sb_user_123",
            "email": "sb@example.com",
            "iss": "https://wrong-issuer",
            "aud": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )

    try:
        __import__("asyncio").get_event_loop().run_until_complete(supabase_auth.verify_supabase_jwt(token))
        assert False, "expected failure"
    except Exception:
        pass

