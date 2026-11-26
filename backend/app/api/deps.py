from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Query, status

from app.config import get_settings


async def require_web_token(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    settings = get_settings()
    expected = (settings.web_api_token or "").strip()
    if not expected or expected in {"replace_me", "changeme"}:
        if settings.allow_placeholder_secrets:
            # Dev-open if token not configured in local dev
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WEB_API_TOKEN is not configured",
        )
    if settings.is_default_web_token and not settings.allow_placeholder_secrets:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WEB_API_TOKEN is still set to an insecure default",
        )
    provided = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1].strip()
    elif token:
        if not getattr(settings, "allow_query_token", True):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Query token authentication is disabled; use Authorization: Bearer header",
            )
        provided = token.strip()
    if not provided:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
