"""Per-request Bearer forwarded from HTTP MCP client → Chronicum API calls."""

from __future__ import annotations

from contextvars import ContextVar

_request_bearer: ContextVar[str | None] = ContextVar("chronicum_request_bearer", default=None)


def set_request_bearer(token: str | None):
    return _request_bearer.set(token)


def reset_request_bearer(token) -> None:
    _request_bearer.reset(token)


def get_request_bearer() -> str | None:
    return _request_bearer.get()
