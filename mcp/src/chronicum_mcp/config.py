from __future__ import annotations

import os

DEFAULT_API_URL = "http://127.0.0.1:8000"
OPEN_TOKENS = frozenset({"", "replace_me", "changeme"})


def api_url() -> str:
    return (os.environ.get("CHRONICUM_API_URL") or DEFAULT_API_URL).rstrip("/")


def api_token() -> str:
    return (
        os.environ.get("CHRONICUM_TOKEN")
        or os.environ.get("WEB_API_TOKEN")
        or ""
    ).strip()


def token_required() -> bool:
    return api_token() not in OPEN_TOKENS
