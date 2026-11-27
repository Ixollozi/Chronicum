from __future__ import annotations

import os

DEFAULT_API_URL = "http://127.0.0.1:8010"
OPEN_TOKENS = frozenset({"", "replace_me", "changeme", "xxx"})
DEFAULT_DEV_TOKEN = "chronicum-dev-token"

_runtime_token: str | None = None
_runtime_api_url: str | None = None


def set_runtime_token(token: str | None) -> None:
    """Register an in-process fallback token (e.g. loaded from backend .env Settings)."""
    global _runtime_token
    _runtime_token = (token or "").strip() if token is not None else None


def set_runtime_api_url(url: str | None) -> None:
    """Register an in-process fallback API base URL (e.g. from backend Settings / port)."""
    global _runtime_api_url
    cleaned = (url or "").strip().rstrip("/") if url is not None else ""
    _runtime_api_url = cleaned or None


def api_url() -> str:
    env_url = (os.environ.get("CHRONICUM_API_URL") or "").strip().rstrip("/")
    if env_url:
        return env_url
    if _runtime_api_url:
        return _runtime_api_url
    for port_var in ("CHRONICUM_API_PORT", "PORT"):
        raw_port = (os.environ.get(port_var) or "").strip()
        if raw_port.isdigit():
            return f"http://127.0.0.1:{raw_port}"
    return DEFAULT_API_URL


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
