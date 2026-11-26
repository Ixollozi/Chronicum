"""API key pools: SQLite CRM keys + .env fallback, Groq → Gemini order."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import get_settings
from app.logging_setup import get_logger
from app.models import ApiKey, AppSetting
from app.services.secrets import decrypt_secret, encrypt_secret, key_hint

log = get_logger(__name__)

PROVIDERS = ("groq", "google")
SETTINGS_DEFAULTS = {
    "default_depth": "standard",
    "auto_moments": "true",
    "moments_vision_gate": "false",
    "video_full": "true",
}

PERSONAL_PROFILE_DEFAULT: dict[str, Any] = {
    "role": "",
    "goals": [],
    "focuses": [],
    "anti_focuses": [],
    "horizon": "90d",
    "current_priorities": "",
}

_HORIZONS = {"now", "90d", "long"}


def normalize_personal_profile(raw: Any) -> dict[str, Any]:
    """Merge and sanitize personal profile dict."""
    base = dict(PERSONAL_PROFILE_DEFAULT)
    if not raw:
        return base
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return base
    if not isinstance(raw, dict):
        return base

    def _str_list(v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in v.replace("\r", "\n").replace(",", "\n").split("\n")]
            return [p for p in parts if p][:40]
        if isinstance(v, list):
            out: list[str] = []
            for item in v:
                s = str(item).strip()
                if s:
                    out.append(s[:200])
                if len(out) >= 40:
                    break
            return out
        return []

    role = str(raw.get("role") or "").strip()[:300]
    horizon = str(raw.get("horizon") or "90d").strip().lower()
    if horizon not in _HORIZONS:
        horizon = "90d"
    priorities = str(raw.get("current_priorities") or "").strip()[:2000]

    return {
        "role": role,
        "goals": _str_list(raw.get("goals")),
        "focuses": _str_list(raw.get("focuses")),
        "anti_focuses": _str_list(raw.get("anti_focuses")),
        "horizon": horizon,
        "current_priorities": priorities,
    }


def profile_is_empty(profile: dict[str, Any] | None) -> bool:
    p = normalize_personal_profile(profile)
    return not (
        p["role"]
        or p["goals"]
        or p["focuses"]
        or p["anti_focuses"]
        or p["current_priorities"]
    )


def format_profile_for_prompt(profile: dict[str, Any] | None) -> str:
    p = normalize_personal_profile(profile)
    if profile_is_empty(p):
        return (
            "PERSONAL PROFILE: (not set)\n"
            "→ Set personal_fit=3 (neutral). Do not invent a persona."
        )
    horizon_label = {"now": "immediate / this week", "90d": "~90 days", "long": "long-term"}.get(
        p["horizon"], p["horizon"]
    )
    lines = [
        "PERSONAL PROFILE (score usefulness FOR this person, not a generic reader):",
        f"- Role: {p['role'] or '—'}",
        f"- Goals: {'; '.join(p['goals']) or '—'}",
        f"- Focuses (boost fit): {'; '.join(p['focuses']) or '—'}",
        f"- Anti-focuses (cut fit hard): {'; '.join(p['anti_focuses']) or '—'}",
        f"- Horizon: {horizon_label}",
        f"- Current priorities: {p['current_priorities'] or '—'}",
    ]
    return "\n".join(lines)


@dataclass
class PooledKey:
    id: int | str
    provider: str
    api_key: str
    label: str | None
    source: str
    enabled: bool = True


_PLACEHOLDERS = {"", "replace_me", "changeme", "xxx"}


def _env_key(provider: str) -> str | None:
    s = get_settings()
    if provider == "groq":
        k = (s.groq_api_key or "").strip()
    else:
        k = (s.google_ai_api_key or "").strip()
    if k.lower() in _PLACEHOLDERS:
        return None
    return k


async def get_app_settings(session: AsyncSession) -> dict:
    rows = (await session.execute(select(AppSetting))).scalars().all()
    data = {r.key: r.value for r in rows}
    profile_raw = data.get("personal_profile")
    return {
        "default_depth": data.get("default_depth", SETTINGS_DEFAULTS["default_depth"]),
        "auto_moments": data.get("auto_moments", SETTINGS_DEFAULTS["auto_moments"]).lower()
        in {"1", "true", "yes"},
        "video_full": data.get("video_full", SETTINGS_DEFAULTS["video_full"]).lower()
        in {"1", "true", "yes"},
        "moments_vision_gate": data.get(
            "moments_vision_gate", SETTINGS_DEFAULTS["moments_vision_gate"]
        ).lower()
        in {"1", "true", "yes"},
        "provider_order": ["groq", "google"],
        "personal_profile": normalize_personal_profile(profile_raw),
    }


async def patch_app_settings(
    session: AsyncSession,
    *,
    default_depth: str | None = None,
    auto_moments: bool | None = None,
    moments_vision_gate: bool | None = None,
    video_full: bool | None = None,
    personal_profile: dict | None = None,
) -> dict:
    if default_depth is not None:
        if default_depth not in {"compact", "standard", "detailed"}:
            raise ValueError("default_depth must be compact|standard|detailed")
        await _upsert_setting(session, "default_depth", default_depth)
    if auto_moments is not None:
        await _upsert_setting(session, "auto_moments", "true" if auto_moments else "false")
    if video_full is not None:
        await _upsert_setting(session, "video_full", "true" if video_full else "false")
    if moments_vision_gate is not None:
        await _upsert_setting(
            session, "moments_vision_gate", "true" if moments_vision_gate else "false"
        )
    if personal_profile is not None:
        normalized = normalize_personal_profile(personal_profile)
        await _upsert_setting(session, "personal_profile", json.dumps(normalized, ensure_ascii=False))
    await session.commit()
    return await get_app_settings(session)


async def _upsert_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AppSetting, key)
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=key, value=value))


async def list_keys(session: AsyncSession, provider: str | None = None) -> list[dict]:
    q = select(ApiKey).order_by(ApiKey.id.desc())
    if provider:
        q = q.where(ApiKey.provider == provider)
    rows = (await session.execute(q)).scalars().all()
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "provider": r.provider,
                "label": r.label,
                "hint": r.key_hint,
                "enabled": r.enabled,
                "source": r.source,
                "last_ok_at": r.last_ok_at,
                "last_error": r.last_error,
            }
        )
    # virtual env keys
    for p in PROVIDERS:
        if provider and p != provider:
            continue
        ek = _env_key(p)
        if ek:
            out.append(
                {
                    "id": f"env:{p}",
                    "provider": p,
                    "label": ".env",
                    "hint": key_hint(ek),
                    "enabled": True,
                    "source": "env",
                    "last_ok_at": None,
                    "last_error": None,
                }
            )
    return out


def _parse_keys_blob(blob: str) -> list[str]:
    parts: list[str] = []
    for line in blob.replace(",", "\n").splitlines():
        k = line.strip()
        if k:
            parts.append(k)
    # unique preserve order
    seen: set[str] = set()
    uniq = []
    for k in parts:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


async def add_keys(session: AsyncSession, provider: str, keys_blob: str, label: str | None) -> int:
    if provider not in PROVIDERS:
        raise ValueError("provider must be groq|google")
    keys = _parse_keys_blob(keys_blob)
    if not keys:
        raise ValueError("No keys provided")
    n = 0
    for k in keys:
        session.add(
            ApiKey(
                provider=provider,
                label=(label.strip() if label else None) or None,
                key_enc=encrypt_secret(k),
                key_hint=key_hint(k),
                enabled=True,
                source="crm",
            )
        )
        n += 1
    await session.commit()
    return n


async def patch_key(session: AsyncSession, key_id: int, *, enabled: bool | None = None, label: str | None = None) -> ApiKey:
    row = await session.get(ApiKey, key_id)
    if not row:
        raise ValueError("Key not found")
    if enabled is not None:
        row.enabled = enabled
    if label is not None:
        row.label = label.strip() or None
    await session.commit()
    await session.refresh(row)
    return row


async def delete_key(session: AsyncSession, key_id: int) -> None:
    row = await session.get(ApiKey, key_id)
    if not row:
        raise ValueError("Key not found")
    if row.source == "env":
        raise ValueError("Env keys cannot be deleted here")
    await session.delete(row)
    await session.commit()


_rr_counters: dict[str, int] = {}


async def _commit_isolated(session: AsyncSession, fn) -> None:
    """
    Persist key-pool metadata without committing the caller's open Material transaction.
    Uses a short-lived sibling session on the same engine whenever safe; if the caller
    already holds an open SQLite transaction, mutates inside the caller's session
    without issuing an early commit (preventing premature commits AND SQLite lock deadlocks).
    """
    bind = session.get_bind()
    is_sqlite = getattr(getattr(bind, "dialect", None), "name", "") == "sqlite"
    if is_sqlite and (session.in_transaction() or session.new or session.dirty or session.deleted):
        await fn(session)
        await session.flush()
        return
    async_bind = bind if isinstance(bind, AsyncEngine) else AsyncEngine(bind)
    async with AsyncSession(async_bind, expire_on_commit=False) as iso:
        await fn(iso)
        await iso.commit()


async def acquire_keys(session: AsyncSession, provider: str) -> list[PooledKey]:
    """Ordered list of usable keys for provider (CRM enabled + env) without committing caller session."""
    rows = (
        await session.execute(
            select(ApiKey).where(ApiKey.provider == provider, ApiKey.enabled.is_(True)).order_by(ApiKey.id.asc())
        )
    ).scalars().all()
    pooled: list[PooledKey] = []
    for r in rows:
        try:
            plain = decrypt_secret(r.key_enc)
        except Exception as e:
            log.warning("key_decrypt_failed", id=r.id, error=str(e)[:120])
            continue
        pooled.append(
            PooledKey(id=r.id, provider=provider, api_key=plain, label=r.label, source=r.source, enabled=True)
        )
    ek = _env_key(provider)
    if ek:
        pooled.append(PooledKey(id=f"env:{provider}", provider=provider, api_key=ek, label=".env", source="env"))

    if not pooled:
        return []

    start = _rr_counters.get(provider, 0) % len(pooled)
    ordered = pooled[start:] + pooled[:start]
    _rr_counters[provider] = (start + 1) % len(pooled)
    return ordered


async def mark_key_result(session: AsyncSession, key_id: int | str, *, ok: bool, error: str | None = None) -> None:
    if isinstance(key_id, str):
        return

    async def _update(s: AsyncSession) -> None:
        row = await s.get(ApiKey, key_id)
        if not row:
            return
        if ok:
            row.last_ok_at = datetime.now(timezone.utc)
            row.last_error = None
        else:
            row.last_error = (error or "error")[:500]

    await _commit_isolated(session, _update)


async def any_provider_ready(session: AsyncSession) -> bool:
    for p in PROVIDERS:
        if _env_key(p):
            return True
        row = (
            await session.execute(
                select(ApiKey.id).where(ApiKey.provider == p, ApiKey.enabled.is_(True)).limit(1)
            )
        ).first()
        if row:
            return True
    return False
