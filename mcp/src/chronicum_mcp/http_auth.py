from __future__ import annotations

import json

from chronicum_mcp.config import OPEN_TOKENS
from chronicum_mcp.request_token import reset_request_bearer, set_request_bearer


class BearerAuthMiddleware:
    """Require Authorization: Bearer on HTTP MCP when a real server token is set.

    Accepts:
    - exact match to configured CHRONICUM_TOKEN / WEB_API_TOKEN (admin/dev)
    - any other non-empty Bearer (personal API token / JWT) — validated by the API
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token.strip()
        self.required = self.token not in OPEN_TOKENS

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.required:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers") or []}
        auth = headers.get("authorization", "")
        provided = ""
        if auth.lower().startswith("bearer "):
            provided = auth.split(" ", 1)[1].strip()

        expected = f"Bearer {self.token}"
        ok = auth == expected or bool(provided)
        if not ok:
            body = json.dumps({"error": "invalid_token", "error_description": "Bearer token required"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b'Bearer error="invalid_token"'),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        token = set_request_bearer(provided or self.token)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_bearer(token)
