"""Unit tests for archive graph (tag hubs + shared tools/links)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.graph import _add_material_shared_edges, _link_host  # noqa: E402


def test_link_host_skips_generic():
    assert _link_host({"url": "https://figma.com/file/x"}) == "figma.com"
    assert _link_host({"url": "https://www.google.com/"}) == ""
    assert _link_host("https://cloth.ai") == "cloth.ai"


def test_shared_tool_edges_connect_materials():
    links: list[dict] = []
    degree: dict[str, int] = {}

    def touch(a: str, b: str) -> None:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1

    tool_materials = {
        "claude": {"m1", "m2", "m3"},
        "figma": {"m2", "m4"},
    }
    link_materials = {
        "figma.com": {"m2", "m4"},
    }
    _add_material_shared_edges(
        tool_materials=tool_materials,
        link_materials=link_materials,
        links=links,
        touch=touch,
    )
    reasons = {li["reason"] for li in links}
    assert "tool" in reasons
    pairs = {(li["source"], li["target"]) for li in links}
    # At least one edge between materials that share Claude
    assert any({"m1", "m2"} <= set(p) or set(p) == {"m1", "m2"} for p in pairs) or any(
        "m1" in p and "m2" in p for p in pairs
    )
    assert any(li["reason"] in {"tool", "link"} for li in links)
    assert len(links) >= 1
