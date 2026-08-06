"""Encrypt API keys at rest (Fernet, key derived from WEB_API_TOKEN)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    token = (get_settings().web_api_token or "chronicum-dev-token").encode("utf-8")
    digest = hashlib.sha256(token).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(blob: str) -> str:
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Cannot decrypt API key — WEB_API_TOKEN changed?") from e


def key_hint(plain: str) -> str:
    plain = plain.strip()
    if len(plain) <= 4:
        return plain
    return plain[-4:]
