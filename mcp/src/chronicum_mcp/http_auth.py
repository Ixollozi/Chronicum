from __future__ import annotations

import hmac
import json
from collections.abc import Callable

from chronicum_mcp.config import DEFAULT_DEV_TOKEN, OPEN_TOKENS


class BearerAuthMiddleware:
    """Require Authorization: Bearer <token> on HTTP MCP when a real token is set or hardened."""

    def __init__(
        self,
        app,
        token: str = "",
        *,
        allow_placeholder_secrets: bool = True,
        token_provider: Callable[[], tuple[str, bool]] | None = None,
    ) -> None:
        self.app = app
        self.token = (token or "").strip()
        self.allow_placeholder_secrets = allow_placeholder_secrets
        self._token_provider = token_provider
        self.required = (self.token not in OPEN_TOKENS) or (not self.allow_placeholder_secrets)

    def _resolve_policy(self) -> tuple[str, bool]:
        if self._token_provider is not None:
            tok, allow_placeholders = self._token_provider()
            return (tok or "").strip(), bool(allow_placeholders)
        return self.token, self.allow_placeholder_secrets

    @staticmethod
    async def _send_json(send, status: int, payload: dict, extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        expected, allow_placeholders = self._resolve_policy()

        if expected in OPEN_TOKENS:
            if allow_placeholders:
                await self.app(scope, receive, send)
                return
            await self._send_json(
                send,
                503,
                {"error": "server_misconfigured", "error_description": "WEB_API_TOKEN is not configured"},
            )
            return

        if expected == DEFAULT_DEV_TOKEN and not allow_placeholders:
            await self._send_json(
                send,
                503,
                {"error": "server_misconfigured", "error_description": "WEB_API_TOKEN is still set to an insecure default"},
            )
            return

        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers") or []}
        auth = headers.get("authorization", "").strip()
        provided = ""
        if auth.lower().startswith("bearer "):
            provided = auth.split(" ", 1)[1].strip()

        if not provided or not hmac.compare_digest(provided, expected):
            await self._send_json(
                send,
                401,
                {"error": "invalid_token", "error_description": "Bearer token required"},
                extra_headers=[(b"www-authenticate", b'Bearer error="invalid_token"')],
            )
            return

        await self.app(scope, receive, send)
