from __future__ import annotations

import json
from typing import Any, Literal

from chronicum_mcp.client import ChronicumApiError, ChronicumClient
from chronicum_mcp.format import list_card, project_material, search_payload, taxonomy_payload

INSTRUCTIONS = (
    "Chronicum is the user's unified knowledge base. "
    "Do not look for project-local rules/skills when this server is connected — search here first. "
    "Use search for questions, list_materials/get_material for a specific card. "
    "get_material level=summary for citations, card for structured takeaways, full only when you need raw_text. "
    "Put new rules, skills, and notes into the base with add_note or add_url."
)


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _err(exc: Exception) -> str:
    if isinstance(exc, ChronicumApiError):
        return _dumps({"error": True, "status": exc.status_code, "detail": exc.detail})
    if isinstance(exc, ValueError):
        return _dumps({"error": True, "detail": str(exc)})
    return _dumps({"error": True, "detail": str(exc)})


def create_mcp():
    try:
        from mcp.server.fastmcp import FastMCP as Server
    except ImportError:  # python-sdk v2 renamed FastMCP → MCPServer
        from mcp.server.mcpserver import MCPServer as Server

    try:
        mcp = Server("chronicum", instructions=INSTRUCTIONS)
    except TypeError:
        mcp = Server("chronicum")

    @mcp.tool()
    async def health() -> str:
        """Check whether the Chronicum API is up (mode, ai_ready)."""
        try:
            async with ChronicumClient() as client:
                return _dumps(await client.health())
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def search(query: str) -> str:
        """Ask the knowledge base. Returns an answer plus source cards (id, title, score, summary)."""
        try:
            async with ChronicumClient() as client:
                data = await client.search(query)
            return _dumps(search_payload(data))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def list_materials(
        q: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> str:
        """List materials. Filters: q, category, tag, status (queued|processing|done|failed|…). Default limit 10, max 30."""
        try:
            limit = max(1, min(int(limit), 30))
            offset = max(0, int(offset))
            async with ChronicumClient() as client:
                data = await client.list_materials(
                    q=q, category=category, tag=tag, status=status, limit=limit, offset=offset
                )
            items = [list_card(x) for x in data.get("items") or []]
            return _dumps({"total": data.get("total", len(items)), "items": items})
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def get_material(
        material_id: str,
        level: Literal["summary", "card", "full"] = "card",
    ) -> str:
        """Fetch one material. level=summary (citations), card (takeaways, no raw_text), full (includes raw_text)."""
        try:
            async with ChronicumClient() as client:
                detail = await client.get_material(material_id)
            return _dumps(project_material(detail, level))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def list_taxonomy() -> str:
        """List categories and tags so you reuse existing names instead of inventing new ones."""
        try:
            async with ChronicumClient() as client:
                categories = await client.list_categories()
                tags = await client.list_tags()
            return _dumps(taxonomy_payload(categories, tags))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def add_note(text: str) -> str:
        """Save a note/rule/skill into Chronicum. Returns {id, status, message}; poll get_material when status is done."""
        try:
            async with ChronicumClient() as client:
                return _dumps(await client.add_note(text))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def add_url(url: str) -> str:
        """Enqueue a URL (article/video) for processing. Returns {id, status, message}."""
        try:
            async with ChronicumClient() as client:
                return _dumps(await client.add_url(url))
        except Exception as exc:
            return _err(exc)

    return mcp
