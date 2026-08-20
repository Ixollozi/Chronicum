import pytest

from chronicum_mcp.tools import create_mcp

EXPECTED = {"health", "search", "list_materials", "get_material", "list_taxonomy", "add_note", "add_url"}


@pytest.mark.asyncio
async def test_create_mcp_registers_v1_tools():
    mcp = create_mcp()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED <= names
