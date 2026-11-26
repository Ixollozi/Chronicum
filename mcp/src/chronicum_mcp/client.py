from __future__ import annotations

from typing import Any

import httpx

from chronicum_mcp.config import api_token, api_url
from chronicum_mcp.request_token import get_request_bearer


class ChronicumApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Chronicum API {status_code}: {detail}")


class ChronicumClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = (base_url or api_url()).rstrip("/")
        forwarded = get_request_bearer()
        self.token = token if token is not None else (forwarded or api_token())
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ChronicumClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise ChronicumApiError(503, f"cannot reach API at {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            detail = _error_detail(response)
            raise ChronicumApiError(response.status_code, detail)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def search(self, query: str) -> dict[str, Any]:
        return await self._request("POST", "/api/search", json={"query": query})

    async def list_materials(
        self,
        *,
        q: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if q:
            params["q"] = q
        if category:
            params["category"] = category
        if tag:
            params["tag"] = tag
        if status:
            params["status"] = status
        return await self._request("GET", "/api/materials", params=params)

    async def get_material(self, material_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/materials/{material_id}")

    async def explain_material(self, material_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/materials/{material_id}/explain")

    async def list_categories(self) -> list[Any]:
        return await self._request("GET", "/api/categories")

    async def list_tags(self, status: str | None = None) -> list[Any]:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        return await self._request("GET", "/api/tags", params=params)

    async def add_note(self, text: str) -> dict[str, Any]:
        return await self._request("POST", "/api/materials/note", json={"text": text})

    async def add_url(self, url: str) -> dict[str, Any]:
        return await self._request("POST", "/api/materials/url", json={"url": url})

    async def list_clients(self, *, q: str | None = None, limit: int = 50) -> list[Any]:
        params: dict[str, Any] = {"limit": limit}
        if q:
            params["q"] = q
        return await self._request("GET", "/api/clients", params=params)

    async def create_client(
        self,
        display_name: str,
        *,
        aliases: list[str] | None = None,
        context_line: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"display_name": display_name, "aliases": aliases or [], "force": force}
        if context_line:
            payload["context_line"] = context_line
        return await self._request("POST", "/api/clients", json=payload)

    async def update_client(self, client_id: str, **fields: Any) -> dict[str, Any]:
        return await self._request("PATCH", f"/api/clients/{client_id}", json=fields)

    async def resolve_client(self, q: str) -> dict[str, Any]:
        return await self._request("GET", "/api/clients/resolve", params={"q": q})

    async def append_client_message(
        self, client_id: str, body: str, *, refresh_summary: bool = False
    ) -> dict[str, Any]:
        detail = await self._request(
            "POST",
            f"/api/clients/{client_id}/messages",
            json={"body": body, "refresh_summary": refresh_summary, "source": "mcp"},
        )
        msgs = detail.get("messages") or []
        return {
            "client_id": detail.get("id", client_id),
            "message_id": msgs[-1].get("id") if msgs else None,
            "message_count": detail.get("message_count", len(msgs)),
        }

    async def get_client_context(
        self, query_or_id: str, *, n: int = 20, search: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query_or_id, "n": n}
        if search:
            params["search"] = search
        return await self._request("GET", "/api/clients/resolve/context", params=params)

    async def refresh_client_summary(self, client_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/clients/{client_id}/summary/refresh")


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:500] or response.reason_phrase
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
    return str(payload)[:500]
