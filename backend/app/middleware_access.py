"""HTTP access + auth outcome logging (stdout JSON + optional file via logging_setup)."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_setup import get_logger

log = get_logger("chronicum.http")

# Noisy / high-frequency paths — still log failures, skip OK chatter
_QUIET_OK = {
    "/health",
    "/api/auth/status",
    "/api/materials/busy",
}


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        auth_hdr = request.headers.get("authorization") or ""
        has_bearer = auth_hdr.lower().startswith("bearer ") and len(auth_hdr) > 8
        path = request.url.path

        try:
            response = await call_next(request)
        except Exception:
            ms = int((time.perf_counter() - start) * 1000)
            log.exception(
                "http_unhandled",
                request_id=req_id,
                method=request.method,
                path=path,
                has_bearer=has_bearer,
                duration_ms=ms,
            )
            raise

        ms = int((time.perf_counter() - start) * 1000)
        status = response.status_code
        quiet = path in _QUIET_OK or path.startswith("/mcp")
        if status >= 400 or not quiet:
            log.info(
                "http_request",
                request_id=req_id,
                method=request.method,
                path=path,
                status=status,
                has_bearer=has_bearer,
                duration_ms=ms,
            )
        response.headers["X-Request-Id"] = req_id
        return response
