"""Optional Streamable HTTP MCP mounted on the FastAPI app at /mcp."""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_mcp: Any = None
_asgi: Any = None


def _resolve_runtime_api_url(settings: Any) -> str | None:
    explicit = (getattr(settings, "chronicum_api_url", "") or "").strip().rstrip("/")
    if explicit:
        return explicit
    port = getattr(settings, "chronicum_api_port", None)
    if port:
        return f"http://127.0.0.1:{int(port)}"
    return None


def _current_mcp_auth_policy() -> tuple[str, bool]:
    settings = get_settings()
    token = (settings.web_api_token or "").strip()
    try:
        from chronicum_mcp.config import set_runtime_api_url, set_runtime_token

        set_runtime_token(token)
        set_runtime_api_url(_resolve_runtime_api_url(settings))
    except Exception:
        pass
    return token, bool(settings.allow_placeholder_secrets)


def build_mcp_asgi() -> tuple[Any, Any] | tuple[None, None]:
    global _mcp, _asgi
    if _mcp is not None:
        return _mcp, _asgi
    try:
        from chronicum_mcp.config import set_runtime_token
        from chronicum_mcp.http_auth import BearerAuthMiddleware
        from chronicum_mcp.tools import create_mcp
    except ImportError as exc:
        log.warning("mcp_package_missing", error=str(exc)[:200])
        return None, None

    try:
        token, allow_placeholders = _current_mcp_auth_policy()
        set_runtime_token(token)
        mcp = create_mcp()
        app = mcp.streamable_http_app(streamable_http_path="/")
        if hasattr(app, "add_middleware"):
            app.add_middleware(
                BearerAuthMiddleware,
                token=token,
                allow_placeholder_secrets=allow_placeholders,
                token_provider=_current_mcp_auth_policy,
            )
        else:
            app = BearerAuthMiddleware(
                app,
                token=token,
                allow_placeholder_secrets=allow_placeholders,
                token_provider=_current_mcp_auth_policy,
            )
        _mcp, _asgi = mcp, app
        log.info("mcp_http_ready", path="/mcp")
        return mcp, app
    except Exception as exc:
        log.warning("mcp_http_failed", error=str(exc)[:300])
        return None, None


def mcp_http_enabled() -> bool:
    return _asgi is not None
