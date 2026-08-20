from __future__ import annotations

from typing import Any

import httpx

from chronicum_mcp.config import api_token, api_url


class ChronicumApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Chronicum API {status_code}: {detail}")


class ChronicumClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = (base_url or api_url()).rstrip("/")
        self.token = token if token is not None else api_token()
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
