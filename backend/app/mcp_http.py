"""Optional Streamable HTTP MCP mounted on the FastAPI app at /mcp."""

from __future__ import annotations

from typing import Any

from app.logging_setup import get_logger

log = get_logger(__name__)

_mcp: Any = None
_asgi: Any = None


def build_mcp_asgi() -> tuple[Any, Any] | tuple[None, None]:
    global _mcp, _asgi
    if _mcp is not None:
        return _mcp, _asgi
    try:
        from chronicum_mcp.config import api_token
        from chronicum_mcp.http_auth import BearerAuthMiddleware
        from chronicum_mcp.tools import create_mcp
    except ImportError as exc:
        log.warning("mcp_package_missing", error=str(exc)[:200])
        return None, None

    try:
        mcp = create_mcp()
        app = mcp.streamable_http_app(streamable_http_path="/")
        if hasattr(app, "add_middleware"):
            app.add_middleware(BearerAuthMiddleware, token=api_token())
        else:
            app = BearerAuthMiddleware(app, api_token())
        _mcp, _asgi = mcp, app
        log.info("mcp_http_ready", path="/mcp")
        return mcp, app
    except Exception as exc:
        log.warning("mcp_http_failed", error=str(exc)[:300])
        return None, None


def mcp_http_enabled() -> bool:
    return _asgi is not None
