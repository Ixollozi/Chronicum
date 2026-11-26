from __future__ import annotations

import json

import httpx
import pytest

from chronicum_mcp.client import ChronicumApiError, ChronicumClient
from chronicum_mcp.format import project_material, search_payload
from chronicum_mcp.http_auth import BearerAuthMiddleware


def _router(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/health":
        return httpx.Response(200, json={"status": "ok", "mode": "lite", "ai_ready": True})
    if path == "/api/search":
        body = json.loads(request.content.decode())
        assert body["query"] == "mcp"
        return httpx.Response(
            200,
            json={
                "answer": "Put skills in Chronicum.",
                "sources": [
                    {
                        "id": "m1",
                        "title": "Skills",
                        "usefulness_score": 8,
                        "processing_status": "done",
                        "summary": "one source",
                        "tags": [],
                        "source_type": "note",
                    }
                ],
            },
        )
    if path == "/api/materials" and request.method == "GET":
        assert request.url.params.get("limit") == "10"
        return httpx.Response(
            200,
            json={
                "total": 1,
                "items": [
                    {
                        "id": "m1",
                        "title": "Skills",
                        "usefulness_score": 8,
                        "processing_status": "done",
                        "summary": "one source",
                        "tags": ["mcp"],
                        "source_type": "note",
                        "category": "AI",
                    }
                ],
            },
        )
    if path == "/api/materials/m1":
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "title": "Skills",
                "source_type": "note",
                "source_url": None,
                "summary": "short",
                "main_idea": "idea",
                "category": "AI",
                "tags": ["mcp"],
                "usefulness_score": 8,
                "processing_status": "done",
                "processing_stage": "full",
                "digest": "bullets",
                "practical_takeaways": ["reuse tags"],
                "raw_text": "the full note",
            },
        )
    if path == "/api/categories":
        return httpx.Response(200, json=[{"id": 1, "name": "AI"}])
    if path == "/api/tags":
        return httpx.Response(200, json=[{"id": 1, "name": "mcp", "status": "approved"}])
    if path == "/api/materials/note":
        body = json.loads(request.content.decode())
        assert "text" in body
        return httpx.Response(200, json={"id": "new1", "status": "queued", "message": "Note accepted"})
    if path == "/api/materials/url":
        return httpx.Response(200, json={"id": "new2", "status": "queued", "message": "URL accepted"})
    if path == "/api/materials/missing":
        return httpx.Response(404, json={"detail": "Not found"})
    return httpx.Response(404, json={"detail": "no mock"})


@pytest.fixture
def client() -> ChronicumClient:
    transport = httpx.MockTransport(_router)
    c = ChronicumClient(base_url="http://chronicum.test", token="chronicum-dev-token")
    c._client = httpx.AsyncClient(
        transport=transport,
        base_url="http://chronicum.test",
        headers={"Authorization": "Bearer chronicum-dev-token", "Accept": "application/json"},
    )
    return c


@pytest.mark.asyncio
async def test_health_and_search(client: ChronicumClient):
    health = await client.health()
    assert health["mode"] == "lite"
    data = await client.search("mcp")
    shaped = search_payload(data)
    assert shaped["answer"].startswith("Put skills")
    assert shaped["sources"][0]["id"] == "m1"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_material_levels(client: ChronicumClient):
    detail = await client.get_material("m1")
    card = project_material(detail, "card")
    assert card["digest"] == "bullets"
    assert "raw_text" not in card
    full = project_material(detail, "full")
    assert full["raw_text"] == "the full note"
    await client.aclose()


@pytest.mark.asyncio
async def test_list_and_taxonomy_and_writes(client: ChronicumClient):
    listed = await client.list_materials(limit=10)
    assert listed["total"] == 1
    cats = await client.list_categories()
    tags = await client.list_tags()
    assert cats[0]["name"] == "AI"
    assert tags[0]["name"] == "mcp"
    note = await client.add_note("rule: always search Chronicum")
    assert note["id"] == "new1"
    url = await client.add_url("https://example.com/a")
    assert url["status"] == "queued"
    await client.aclose()


@pytest.mark.asyncio
async def test_api_error(client: ChronicumClient):
    with pytest.raises(ChronicumApiError) as ei:
        await client.get_material("missing")
    assert ei.value.status_code == 404
    assert "Not found" in ei.value.detail
    await client.aclose()


@pytest.mark.asyncio
async def test_bearer_middleware_rejects():
    async def dummy(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerAuthMiddleware(dummy, "secret-token")
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [],
        "path": "/mcp",
    }
    await app(scope, receive, send)
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_bearer_middleware_forwards_personal_token():
    """Non-matching Bearer (PAT/JWT) is accepted here; API validates ownership."""

    async def dummy(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerAuthMiddleware(dummy, "secret-token")
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"authorization", b"Bearer chr_personal_token")],
        "path": "/mcp",
    }
    await app(scope, receive, send)
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_bearer_middleware_accepts():
    async def dummy(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerAuthMiddleware(dummy, "secret-token")
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"authorization", b"Bearer secret-token")],
        "path": "/mcp",
    }
    await app(scope, receive, send)
    assert sent[0]["status"] == 204
