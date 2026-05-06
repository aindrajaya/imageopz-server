from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, status

from app.utils.supabase_auth import SupabaseIdentity, verify_supabase_jwt
import logging

logger = logging.getLogger("imageopz.auth")


async def get_optional_supabase_identity(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Optional[SupabaseIdentity]:
    """
    Optional Supabase auth dependency.
    - If header missing: returns None
    - If header present but invalid: raises 401
    """
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization header")
    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    # Supabase client libraries may send API keys in Authorization when unauthenticated.
    # Treat non-JWT bearer tokens as "no identity" to keep guest mode working.
    if token.count(".") != 2:
        return None
    try:
        return await verify_supabase_jwt(token)
    except HTTPException:
        raise
    except Exception as e:
        try:
            logger.warning("Supabase JWT verify failed: %s", str(e))
        except Exception:
            pass
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid Supabase JWT: {str(e)}")
