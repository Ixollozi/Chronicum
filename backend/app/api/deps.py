from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.logging_setup import get_logger
from app.services.auth import AuthContext, decode_session_jwt, get_user, lookup_api_token

# Re-export for callers
__all__ = ["AuthContext", "require_auth", "require_web_token", "scope_user_id"]

log = get_logger(__name__)

_PLACEHOLDER_TOKENS = {"", "replace_me", "changeme"}


def _extract_bearer(
    authorization: str | None,
    token: str | None,
    *,
    allow_query_token: bool = True,
) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() or None
    if token:
        if not allow_query_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Query token authentication is disabled; use Authorization: Bearer header",
            )
        return token.strip() or None
    return None


async def require_auth(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Accept session JWT or personal API token.

    Legacy WEB_API_TOKEN (admin, sees all) only when ALLOW_ADMIN_TOKEN=true,
    or when Google OAuth is not configured yet (local bootstrap).
    """
    settings = get_settings()
    admin_token = (settings.web_api_token or "").strip()
    admin_placeholder = admin_token in _PLACEHOLDER_TOKENS
    google_on = settings.google_oauth_ready
    admin_allowed = (not google_on) or settings.allow_admin_token

    if not google_on and not settings.allow_placeholder_secrets:
        if admin_placeholder:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WEB_API_TOKEN is not configured",
            )
        if settings.is_default_web_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WEB_API_TOKEN is still set to an insecure default",
            )

    provided = _extract_bearer(
        authorization,
        token,
        allow_query_token=bool(getattr(settings, "allow_query_token", True)),
    )

    if not provided:
        # Open only when Google is off AND token is still a placeholder in dev mode.
        if not google_on and admin_placeholder and settings.allow_placeholder_secrets:
            return AuthContext(user_id=None, is_admin=True)
        log.info("auth_rejected", reason="missing_bearer", google_oauth=google_on)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")

    if (
        admin_allowed
        and not admin_placeholder
        and hmac.compare_digest(provided, admin_token)
    ):
        log.debug("auth_ok", kind="admin")
        return AuthContext(user_id=None, is_admin=True)

    jwt_payload = decode_session_jwt(provided, settings)
    if jwt_payload:
        user = await get_user(session, str(jwt_payload["sub"]))
        if not user:
            log.warning("auth_rejected", reason="user_not_found", sub=str(jwt_payload.get("sub")))
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        log.debug("auth_ok", kind="jwt", user_id=user.id)
        return AuthContext(
            user_id=user.id,
            is_admin=False,
            email=user.email,
            name=user.name,
            picture_url=user.picture_url,
        )

    pat = await lookup_api_token(session, provided)
    if pat:
        user, _tok = pat
        log.debug("auth_ok", kind="pat", user_id=user.id)
        return AuthContext(
            user_id=user.id,
            is_admin=False,
            email=user.email,
            name=user.name,
            picture_url=user.picture_url,
        )

    log.info(
        "auth_rejected",
        reason="invalid_token",
        google_oauth=google_on,
        admin_allowed=admin_allowed,
        prefix=(provided[:4] + "…" if len(provided) > 4 else "short"),
    )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def require_web_token(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Back-compat alias — same as require_auth."""
    return await require_auth(authorization=authorization, token=token, session=session)


def scope_user_id(auth: AuthContext) -> str | None:
    """Owner id to stamp on creates; None for admin (orphan / shared)."""
    if auth.is_admin:
        return None
    return auth.user_id
