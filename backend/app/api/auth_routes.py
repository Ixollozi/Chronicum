"""Google OAuth + session + personal API tokens (MCP)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_auth
from app.config import get_settings
from app.db import get_session
from app.logging_setup import get_logger
from app.services import auth as auth_svc

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = get_logger(__name__)


class MeOut(BaseModel):
    id: str | None
    email: str | None
    name: str | None
    picture_url: str | None
    is_admin: bool
    google_oauth_ready: bool


class TokenCreate(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class TokenOut(BaseModel):
    id: str
    label: str | None
    token_prefix: str
    created_at: str | None
    last_used_at: str | None


class TokenCreatedOut(TokenOut):
    token: str


@router.get("/google")
async def google_login():
    settings = get_settings()
    if not settings.google_oauth_ready:
        log.warning("google_login_blocked", reason="oauth_not_configured")
        raise HTTPException(
            503,
            "Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        )
    log.info("google_login_redirect")
    return RedirectResponse(auth_svc.google_authorize_url(settings))


@router.get("/google/callback")
async def google_callback(
    code: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    web = (settings.web_origin or "http://127.0.0.1:3000").rstrip("/")
    if error:
        log.warning("google_callback_denied", error=error[:120])
        return RedirectResponse(f"{web}/auth/callback#error={quote(error)}")
    if not code:
        log.warning("google_callback_denied", error="missing_code")
        return RedirectResponse(f"{web}/auth/callback#error=missing_code")
    if not settings.google_oauth_ready:
        log.warning("google_callback_denied", error="oauth_not_configured")
        return RedirectResponse(f"{web}/auth/callback#error=oauth_not_configured")
    try:
        info = await auth_svc.exchange_google_code(code, settings)
        user = await auth_svc.upsert_google_user(session, info)
        token = auth_svc.issue_session_jwt(user, settings)
    except Exception as exc:  # noqa: BLE001
        log.exception("google_callback_failed", error=str(exc)[:200])
        return RedirectResponse(f"{web}/auth/callback#error={quote(str(exc)[:180])}")
    log.info("google_callback_ok", user_id=user.id, email=user.email)
    return RedirectResponse(f"{web}/auth/callback#token={quote(token)}")


@router.get("/me", response_model=MeOut)
async def auth_me(auth: AuthContext = Depends(require_auth)):
    settings = get_settings()
    return MeOut(
        id=auth.user_id,
        email=auth.email,
        name=auth.name,
        picture_url=auth.picture_url,
        is_admin=auth.is_admin,
        google_oauth_ready=settings.google_oauth_ready,
    )


@router.post("/logout")
async def auth_logout():
    """Client clears local token; nothing server-side to revoke for JWT v1."""
    return {"ok": True}


@router.get("/tokens", response_model=list[TokenOut])
async def list_tokens(
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    if not auth.user_id:
        raise HTTPException(400, "Admin/dev token cannot mint personal MCP tokens — sign in with Google")
    rows = await auth_svc.list_api_tokens(session, auth.user_id)
    return [
        TokenOut(
            id=r.id,
            label=r.label,
            token_prefix=r.token_prefix,
            created_at=r.created_at.isoformat() if r.created_at else None,
            last_used_at=r.last_used_at.isoformat() if r.last_used_at else None,
        )
        for r in rows
    ]


@router.post("/tokens", response_model=TokenCreatedOut)
async def create_token(
    body: TokenCreate,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    if not auth.user_id:
        raise HTTPException(400, "Sign in with Google to create a personal MCP token")
    row, raw = await auth_svc.create_api_token(session, auth.user_id, label=body.label)
    log.info("pat_created", user_id=auth.user_id, token_id=row.id, label=row.label)
    return TokenCreatedOut(
        id=row.id,
        label=row.label,
        token_prefix=row.token_prefix,
        created_at=row.created_at.isoformat() if row.created_at else None,
        last_used_at=None,
        token=raw,
    )


@router.delete("/tokens/{token_id}")
async def delete_token(
    token_id: str,
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
):
    if not auth.user_id:
        raise HTTPException(400, "Sign in with Google")
    ok = await auth_svc.revoke_api_token(session, auth.user_id, token_id)
    if not ok:
        raise HTTPException(404, "Token not found")
    log.info("pat_revoked", user_id=auth.user_id, token_id=token_id)
    return {"ok": True}


@router.get("/status")
async def auth_status():
    settings = get_settings()
    return {
        "google_oauth_ready": settings.google_oauth_ready,
        "web_origin": settings.web_origin,
    }
