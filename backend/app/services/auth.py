"""Google OAuth, session JWT, and personal API tokens."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import User, UserApiToken

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
PAT_PREFIX = "chr_"
LAST_USED_TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class AuthContext:
    user_id: str | None
    is_admin: bool
    email: str | None = None
    name: str | None = None
    picture_url: str | None = None


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint_api_token_raw() -> str:
    return f"{PAT_PREFIX}{secrets.token_urlsafe(32)}"


def issue_session_jwt(user: User, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture_url,
        "typ": "session",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=max(1, s.jwt_ttl_days))).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_session_jwt(token: str, settings: Settings | None = None) -> dict | None:
    s = settings or get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "session" or not payload.get("sub"):
        return None
    return payload


def google_authorize_url(settings: Settings | None = None, state: str | None = None) -> str:
    s = settings or get_settings()
    from urllib.parse import urlencode

    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state or secrets.token_urlsafe(16),
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_google_code(code: str, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        token_res = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri": s.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_res.raise_for_status()
        tokens = token_res.json()
        access = tokens.get("access_token")
        if not access:
            raise ValueError("Google token response missing access_token")
        info_res = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access}"},
        )
        info_res.raise_for_status()
        return info_res.json()


async def upsert_google_user(session: AsyncSession, info: dict) -> User:
    sub = str(info.get("sub") or "").strip()
    email = str(info.get("email") or "").strip().lower()
    if not sub or not email:
        raise ValueError("Google profile missing sub/email")
    name = (info.get("name") or info.get("given_name") or "").strip() or None
    picture = (info.get("picture") or "").strip() or None

    user = (
        await session.execute(select(User).where(User.google_sub == sub))
    ).scalars().first()
    if user:
        user.email = email
        user.name = name
        user.picture_url = picture
    else:
        user = User(
            id=str(uuid4()),
            google_sub=sub,
            email=email,
            name=name,
            picture_url=picture,
        )
        session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def lookup_api_token(session: AsyncSession, raw: str) -> tuple[User, UserApiToken] | None:
    digest = hash_api_token(raw)
    row = (
        await session.execute(
            select(UserApiToken, User)
            .join(User, User.id == UserApiToken.user_id)
            .where(UserApiToken.token_hash == digest, UserApiToken.revoked_at.is_(None))
        )
    ).first()
    if not row:
        return None
    token, user = row
    now = datetime.now(timezone.utc)
    last_used = token.last_used_at
    if last_used is not None and last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=timezone.utc)
    if last_used is None or (now - last_used) >= LAST_USED_TOUCH_INTERVAL:
        token.last_used_at = now
        try:
            await session.commit()
        except OperationalError:
            await session.rollback()
            refreshed = (
                await session.execute(
                    select(UserApiToken, User)
                    .join(User, User.id == UserApiToken.user_id)
                    .where(UserApiToken.token_hash == digest, UserApiToken.revoked_at.is_(None))
                )
            ).first()
            if refreshed:
                token, user = refreshed
    return user, token


async def list_api_tokens(session: AsyncSession, user_id: str) -> list[UserApiToken]:
    rows = (
        await session.execute(
            select(UserApiToken)
            .where(UserApiToken.user_id == user_id, UserApiToken.revoked_at.is_(None))
            .order_by(UserApiToken.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def create_api_token(
    session: AsyncSession, user_id: str, *, label: str | None = None
) -> tuple[UserApiToken, str]:
    raw = mint_api_token_raw()
    row = UserApiToken(
        id=str(uuid4()),
        user_id=user_id,
        token_hash=hash_api_token(raw),
        token_prefix=raw[:10],
        label=(label or "").strip() or None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, raw


async def revoke_api_token(session: AsyncSession, user_id: str, token_id: str) -> bool:
    row = (
        await session.execute(
            select(UserApiToken).where(
                UserApiToken.id == token_id,
                UserApiToken.user_id == user_id,
                UserApiToken.revoked_at.is_(None),
            )
        )
    ).scalars().first()
    if not row:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    return True


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return (await session.execute(select(User).where(User.id == user_id))).scalars().first()
