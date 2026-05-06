"""
Supabase JWT verification (offline) using JWKS.

Design goals:
- No network call on each request (JWKS is cached in-memory with TTL)
- Optional identity: endpoints may allow guests when Authorization header is absent
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from typing import Any, Optional

import httpx
import jwt

from app.config import settings


@dataclass(frozen=True)
class SupabaseIdentity:
    sub: str
    email: Optional[str]
    raw_claims: dict[str, Any]


class _JwksCache:
    def __init__(self):
        self._jwks: Optional[dict[str, Any]] = None
        self._fetched_at: float = 0.0

    def is_fresh(self) -> bool:
        ttl = int(getattr(settings, "supabase_jwks_cache_ttl_seconds", 3600) or 3600)
        return bool(self._jwks) and (time.time() - self._fetched_at) < ttl

    async def get_jwks(self) -> dict[str, Any]:
        if self.is_fresh():
            return self._jwks or {"keys": []}

        if not settings.supabase_jwks_url:
            raise RuntimeError("SUPABASE_JWKS_URL is not configured")

        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(settings.supabase_jwks_url)
            res.raise_for_status()
            jwks = res.json()
            if not isinstance(jwks, dict) or "keys" not in jwks:
                raise RuntimeError("Invalid JWKS response")

        self._jwks = jwks
        self._fetched_at = time.time()
        return jwks


_jwks_cache = _JwksCache()

async def _verify_via_auth_server(token: str) -> SupabaseIdentity:
    """
    Fallback verification by calling Supabase Auth server.
    This is required when the project uses a symmetric signing key (HS256),
    because JWKS may return zero keys and offline verification is not possible.
    """
    if not settings.supabase_auth_url or not settings.supabase_anon_key:
        raise RuntimeError("Supabase auth server fallback is not configured (SUPABASE_AUTH_URL/SUPABASE_ANON_KEY)")
    url = settings.supabase_auth_url.rstrip("/") + "/user"
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(
            url,
            headers={
                "apikey": settings.supabase_anon_key,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "imageopz-server/1.0",
            },
        )
    if res.status_code != 200:
        preview = ""
        try:
            txt = res.text or ""
            preview = (txt[:300] + "...") if len(txt) > 300 else txt
        except Exception:
            preview = ""
        raise jwt.InvalidTokenError(
            f"Supabase auth server rejected token (status={res.status_code}) body={preview}"
        )

    # Auth server validated the token; decode claims without signature verification.
    claims = jwt.decode(token, options={"verify_signature": False})
    sub = claims.get("sub")
    if not sub or not isinstance(sub, str):
        raise jwt.InvalidTokenError("Missing sub claim")
    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        email = None
    return SupabaseIdentity(sub=sub, email=email, raw_claims=claims)

async def verify_supabase_jwt(token: str) -> SupabaseIdentity:
    """
    Verify Supabase JWT using the configured JWKS/issuer/audience.
    Raises jwt.InvalidTokenError (or subclasses) on failure.
    """
    # JWKS-based verification requires asymmetric signing keys (e.g. RS256/ES256).
    # If the project uses legacy HS256, JWKS will be empty and we must call the auth server.
    if not settings.supabase_jwks_url or not settings.supabase_jwt_issuer:
        return await _verify_via_auth_server(token)

    unverified_header = jwt.get_unverified_header(token)
    alg = unverified_header.get("alg")
    kid = unverified_header.get("kid")
    if not kid or alg != "RS256":
        return await _verify_via_auth_server(token)

    jwks = await _jwks_cache.get_jwks()
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        raise jwt.InvalidTokenError("Invalid JWKS keys")
    if len(keys) == 0:
        return await _verify_via_auth_server(token)

    key_obj = None
    for k in keys:
        if isinstance(k, dict) and k.get("kid") == kid:
            key_obj = k
            break
    if not key_obj:
        # Refresh once in case of rotation
        _jwks_cache._jwks = None
        jwks = await _jwks_cache.get_jwks()
        keys = jwks.get("keys") if isinstance(jwks, dict) else []
        for k in keys:
            if isinstance(k, dict) and k.get("kid") == kid:
                key_obj = k
                break
    if not key_obj:
        raise jwt.InvalidTokenError("Unknown kid (key not found)")

    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_obj))

    options = {"verify_aud": bool(settings.supabase_jwt_audience)}
    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=settings.supabase_jwt_issuer,
        audience=settings.supabase_jwt_audience or None,
        options=options,
    )

    sub = claims.get("sub")
    if not sub or not isinstance(sub, str):
        raise jwt.InvalidTokenError("Missing sub claim")

    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        email = None

    return SupabaseIdentity(sub=sub, email=email, raw_claims=claims)
