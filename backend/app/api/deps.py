from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Query, status

from app.config import get_settings


async def require_web_token(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    settings = get_settings()
    expected = (settings.web_api_token or "").strip()
    if not expected or expected in {"replace_me", "changeme"}:
        # Dev-open if token not configured
        return
    provided = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1].strip()
    elif token:
        provided = token.strip()
    if not provided:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    if provided != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
