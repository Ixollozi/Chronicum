from __future__ import annotations

import json
from typing import Any, Literal

from chronicum_mcp.client import ChronicumApiError, ChronicumClient
from chronicum_mcp.format import list_card, project_material, search_payload, taxonomy_payload

INSTRUCTIONS = (
    "Chronicum is the user's unified knowledge base + living client memory. "
    "Do not look for project-local rules/skills when this server is connected — search here first. "
    "AUTH: Google OAuth is not available inside MCP. After Sign in with Google in the Chronicum web app, "
    "create a Personal API Token in Settings → Account and put it in the MCP Bearer header / CHRONICUM_TOKEN. "
    "Use search for questions, list_materials/get_material for a specific card. "
    "get_material level=summary for citations, card for structured takeaways, "
    "teach for lesson pack (digest+explanation+tools+links, no raw_text), "
    "full only when you need raw_text. "
    "TRUST: teach or explain only when trust_level is mcp_safe. "
    "If trust_level is review or weak, warn the user and do not invent missing links/tools. "
    "Put new rules, skills, and notes into the base with add_note or add_url. "
    "For people/correspondence use list_clients, resolve_client, create_client, update_client, "
    "append_client_message, refresh_client_summary, get_client_context "
    "(summary + last messages — never dump the whole chat). "
    "If a client lookup is ambiguous, ask the user which person — never guess. "
    "If summary_stale is true, say so and offer refresh_client_summary."
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
        level: Literal["summary", "card", "teach", "full"] = "card",
    ) -> str:
        """Fetch one material. summary|card|teach (lesson pack)|full (includes raw_text). Prefer teach when trust_level=mcp_safe."""
        try:
            async with ChronicumClient() as client:
                detail = await client.get_material(material_id)
            return _dumps(project_material(detail, level))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def explain_material(material_id: str) -> str:
        """Generate/refresh the teacher explanation for a material (POST /explain). Use for review cards on demand."""
        try:
            async with ChronicumClient() as client:
                detail = await client.explain_material(material_id)
            return _dumps(project_material(detail, "teach"))
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

    @mcp.tool()
    async def list_clients(q: str = "", limit: int = 30) -> str:
        """List living client cards (people memory). Optional name/alias filter q."""
        try:
            async with ChronicumClient() as client:
                return _dumps(await client.list_clients(q=q or None, limit=limit))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def create_client(display_name: str, context_line: str = "", force: bool = False) -> str:
        """Create a client card. Returns 409-style error with candidates on possible duplicate unless force."""
        try:
            async with ChronicumClient() as client:
                return _dumps(
                    await client.create_client(
                        display_name, context_line=context_line or None, force=force
                    )
                )
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def resolve_client(q: str) -> str:
        """Resolve a person by name/alias/contact/id. Ambiguous returns candidates — ask the user."""
        try:
            async with ChronicumClient() as client:
                return _dumps(await client.resolve_client(q))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def update_client(
        client_id: str,
        display_name: str = "",
        aliases: str = "",
        context_line: str = "",
        canon_facts: str = "",
    ) -> str:
        """Patch a client card. aliases is comma-separated."""
        fields: dict = {}
        if display_name:
            fields["display_name"] = display_name
        if aliases:
            fields["aliases"] = [a.strip() for a in aliases.split(",") if a.strip()]
        if context_line:
            fields["context_line"] = context_line
        if canon_facts:
            fields["canon_facts"] = canon_facts
        try:
            async with ChronicumClient() as client:
                return _dumps(await client.update_client(client_id, **fields))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def refresh_client_summary(client_id: str) -> str:
        """Rebuild the living summary for a client (pass UUID)."""
        try:
            async with ChronicumClient() as client:
                return _dumps(await client.refresh_client_summary(client_id))
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def append_client_message(client_id: str, text: str, refresh_summary: bool = False) -> str:
        """Append a message to a client thread. Pass client UUID from list_clients."""
        try:
            async with ChronicumClient() as client:
                return _dumps(
                    await client.append_client_message(
                        client_id, text, refresh_summary=refresh_summary
                    )
                )
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    async def get_client_context(query_or_id: str, n: int = 20, search: str = "") -> str:
        """Agent pack for a person: summary + last N messages (+ optional search hits). Use name, alias, or id."""
        try:
            async with ChronicumClient() as client:
                return _dumps(
                    await client.get_client_context(
                        query_or_id, n=n, search=search or None
                    )
                )
        except Exception as exc:
            return _err(exc)

    return mcp
