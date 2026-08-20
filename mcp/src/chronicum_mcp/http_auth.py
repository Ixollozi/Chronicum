from __future__ import annotations

import json

from chronicum_mcp.config import OPEN_TOKENS


class BearerAuthMiddleware:
    """Require Authorization: Bearer <token> on HTTP MCP when a real token is set."""

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
        expected = f"Bearer {self.token}"
        if auth != expected:
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

        await self.app(scope, receive, send)
