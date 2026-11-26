"""Encrypt API keys at rest (Fernet, using SECRETS_ENCRYPTION_KEY or WEB_API_TOKEN fallback)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet_from_secret(secret: str) -> Fernet:
    raw = secret.strip()
    try:
        # Allow users to supply a native 32-byte urlsafe-base64 Fernet key directly
        if len(raw) == 44:
            decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
            if len(decoded) == 32:
                return Fernet(raw.encode("ascii"))
    except Exception:
        pass
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet_candidates() -> list[Fernet]:
    settings = get_settings()
    primary = (getattr(settings, "secrets_encryption_key", "") or "").strip()
    legacy = (settings.web_api_token or "chronicum-dev-token").strip() or "chronicum-dev-token"
    out: list[Fernet] = []
    if primary:
        out.append(_fernet_from_secret(primary))
    if not primary or legacy != primary:
        out.append(_fernet_from_secret(legacy))
    return out


def _fernet() -> Fernet:
    return _fernet_candidates()[0]


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(blob: str) -> str:
    raw = blob.encode("ascii")
    for f in _fernet_candidates():
        try:
            return f.decrypt(raw).decode("utf-8")
        except InvalidToken:
            continue
    raise ValueError(
        "Cannot decrypt API key — check SECRETS_ENCRYPTION_KEY / WEB_API_TOKEN."
    )


def key_hint(plain: str) -> str:
    plain = plain.strip()
    if len(plain) <= 4:
        return plain
    return plain[-4:]
