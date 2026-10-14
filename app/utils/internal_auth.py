"""
Auth adapter for internal service-to-service endpoints (ADR-004).

INTERNAL_AUTH_MODE:
- "shared_secret": X-Internal-Token compared in constant time with INTERNAL_AUTH_SHARED_SECRET (local/tests).
- "oidc": Bearer Google ID token (Cloud Tasks); audience = INTERNAL_AUTH_AUDIENCE, email must be in
  INTERNAL_AUTH_ALLOWED_EMAILS and verified.
- unset or unknown: every request is refused (fail closed).
"""
import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import settings

logger = logging.getLogger("imageopz.internal_auth")


def _verify_oidc_token(token: str, audience: str) -> dict:
    """Thin wrapper over google-auth so tests can fake it (no network)."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, google_requests.Request(), audience=audience)


def _deny() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def require_internal_auth(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> None:
    mode = (settings.internal_auth_mode or "").strip().lower()

    if mode == "shared_secret":
        secret = settings.internal_auth_shared_secret
        if not secret or not x_internal_token:
            raise _deny()
        if not hmac.compare_digest(x_internal_token.encode(), secret.encode()):
            raise _deny()
        return

    if mode == "oidc":
        audience = settings.internal_auth_audience
        allowed = settings.internal_auth_allowed_emails_list()
        parts = (authorization or "").split(" ", 1)
        if not audience or not allowed or len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise _deny()
        try:
            claims = _verify_oidc_token(parts[1].strip(), audience)
        except Exception:
            logger.warning("Internal OIDC token rejected", exc_info=True)
            raise _deny()
        email = str(claims.get("email") or "").lower()
        if not claims.get("email_verified", False) or email not in allowed:
            logger.warning("Internal OIDC token email not allowed: %s", email)
            raise _deny()
        return

    logger.error("Internal request refused: INTERNAL_AUTH_MODE is unset or unknown")
    raise _deny()
