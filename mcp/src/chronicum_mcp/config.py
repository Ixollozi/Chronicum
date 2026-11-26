from __future__ import annotations

import os

DEFAULT_API_URL = "http://127.0.0.1:8000"
OPEN_TOKENS = frozenset({"", "replace_me", "changeme", "xxx"})
DEFAULT_DEV_TOKEN = "chronicum-dev-token"

_runtime_token: str | None = None


def set_runtime_token(token: str | None) -> None:
    """Register an in-process fallback token (e.g. loaded from backend .env Settings)."""
    global _runtime_token
    _runtime_token = (token or "").strip() if token is not None else None


def api_url() -> str:
    return (os.environ.get("CHRONICUM_API_URL") or DEFAULT_API_URL).rstrip("/")


def api_token() -> str:
    env_tok = (
        os.environ.get("CHRONICUM_TOKEN")
        or os.environ.get("WEB_API_TOKEN")
        or ""
    ).strip()
    if env_tok:
        return env_tok
    return (_runtime_token or "").strip()


def is_default_or_open_token(token: str | None = None) -> bool:
    tok = (api_token() if token is None else token).strip()
    return tok in OPEN_TOKENS or tok == DEFAULT_DEV_TOKEN


def token_required() -> bool:
    return api_token() not in OPEN_TOKENS
