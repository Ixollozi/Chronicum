"""Unit tests for archive graph host filtering and IDF shared-tool/link candidate edges."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.graph import (  # noqa: E402
    _compute_link_candidates,
    _compute_shared_tool_candidates,
    _link_host,
)


def test_link_host_skips_generic_platforms():
    for generic in (
        "https://www.google.com/",
        "https://github.com/org/repo",
        "https://x.com/user/status/1",
        "https://twitter.com/user/status/1",
        "https://notion.so/page-123",
        "https://figma.com/file/x",
        "https://my-demo.vercel.app/",
        "https://www.instagram.com/reel/abc",
        "https://youtu.be/xyz",
    ):
        assert _link_host({"url": generic}) == "", f"expected generic host to be skipped: {generic}"

    assert _link_host("https://cloth.ai") == "cloth.ai"
    assert _link_host({"url": "https://www.klingai.com/demo"}) == "klingai.com"


def test_shared_tool_and_link_candidates_no_duplicate_tool_layer():
    entities_by_mid = {
        "m1": {"claude code": "Claude Code", "cursor": "Cursor"},
        "m2": {"claude code": "Claude Code", "figma": "Figma"},
        "m3": {"claude code": "Claude Code"},
        "m4": {"figma": "Figma"},
    }
    tool_edges = _compute_shared_tool_candidates(entities_by_mid)
    reasons = {e["reason"] for e in tool_edges}
    assert reasons == {"shared_tool"}
    assert "tool" not in reasons
    assert any({e["source"], e["target"]} == {"m2", "m4"} for e in tool_edges)

    link_edges = _compute_link_candidates({"klingai.com": {"m2", "m4"}})
    assert len(link_edges) == 1
    assert link_edges[0]["reason"] == "link"
    assert link_edges[0]["label"] == "klingai.com"
