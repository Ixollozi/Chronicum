"""Phase 1 Knowledge Atlas tests: taxonomy cleanup, edge caps, auto-subclustering, 2D layout, filters."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import Base, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Client,
    Material,
    MaterialClientLink,
    MaterialTag,
    ProcessingStatus,
    Tag,
    TagStatus,
)
from app.search.embeddings import LOCAL_EMBED_MODEL, local_embed_text  # noqa: E402
from app.services.graph import build_graph, invalidate_graph_cache  # noqa: E402
from app.services.taxonomy import clean_graph_tags, normalize_key  # noqa: E402


async def _make_session_factory(tmp_path: Path, name: str):
    db_url = f"sqlite+aiosqlite:///{tmp_path / name}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    invalidate_graph_cache()
    return engine, Session


def test_taxonomy_aliases_and_meta_filtering():
    assert normalize_key("искусственный интеллект") == "ai"
    assert normalize_key("ИИ") == "ai"
    assert normalize_key("design") == "дизайн"
    assert normalize_key("Дизайн") == "дизайн"
    assert normalize_key("cloud code") == "claude code"

    cleaned = clean_graph_tags(
        ["AI", "искусственный интеллект", "design", "Дизайн", "reels", "reference", "Claude"],
        category_name="AI",
        include_meta=False,
    )
    assert cleaned == ["Дизайн", "Claude"]

    with_meta = clean_graph_tags(
        ["AI", "design", "Дизайн", "reels", "Claude"],
        category_name="AI",
        include_meta=True,
    )
    assert with_meta == ["Дизайн", "Reels", "Claude"]


@pytest.mark.asyncio
async def test_atlas_filters_pending_tags_removes_hubs_and_caps_degrees(tmp_path: Path):
    engine, Session = await _make_session_factory(tmp_path, "atlas_clean.db")
    try:
        async with Session() as s:
            cat_ai = Category(name="AI")
            cat_mkt = Category(name="Маркетинг")
            s.add_all([cat_ai, cat_mkt])
            await s.flush()

            t_approved = Tag(name="Claude", status=TagStatus.APPROVED.value)
            t_alias = Tag(name="design", status=TagStatus.APPROVED.value)
            t_pending = Tag(name="PendingNoiseTag", status=TagStatus.PENDING.value)
            t_cat_dup = Tag(name="искусственный интеллект", status=TagStatus.APPROVED.value)
            s.add_all([t_approved, t_alias, t_pending, t_cat_dup])
            await s.flush()

            mats: list[Material] = []
            for i in range(12):
                title = f"Claude Code agent workflow #{i}"
                summary = f"Automate coding pipelines with Claude Code, MCP servers, and Cursor rules #{i}"
                m = Material(
                    source_type="instagram",
                    source_url=f"https://instagram.com/reel/ai_{i}",
                    title=title,
                    summary=summary,
                    category_id=cat_ai.id,
                    tools=["Claude Code", "Cursor", "GitHub", "MCP"],
                    technologies=["Python"],
                    links=[
                        {"url": "https://github.com/anthropics/claude-code"},
                        {"url": "https://figma.com/file/shared"},
                        {"url": "https://x.com/post/123"},
                        {"url": "https://notion.so/template"},
                    ],
                    usefulness_score=8,
                    processing_status=ProcessingStatus.DONE.value,
                    embedding={"model": LOCAL_EMBED_MODEL, "vec": local_embed_text(f"{title} {summary}")},
                )
                s.add(m)
                mats.append(m)

            for i in range(2):
                m = Material(
                    source_type="youtube",
                    source_url=f"https://youtube.com/watch?v=mkt_{i}",
                    title=f"Performance ads funnel #{i}",
                    summary="Instagram reels hook retention and creative testing",
                    category_id=cat_mkt.id,
                    tools=["Meta Ads"],
                    links=[{"url": "https://rare-analytics-tool.example/guide"}],
                    usefulness_score=7,
                    processing_status=ProcessingStatus.DONE.value,
                    embedding={"model": LOCAL_EMBED_MODEL, "vec": local_embed_text(f"Performance ads funnel {i}")},
                )
                s.add(m)
                mats.append(m)

            m_uncat = Material(
                source_type="text",
                title="Raw uncategorized note",
                summary="Untagged and uncategorized entry",
                category_id=None,
                usefulness_score=5,
                processing_status=ProcessingStatus.DONE.value,
            )
            s.add(m_uncat)
            await s.flush()

            s.add_all(
                [
                    MaterialTag(material_id=mats[0].id, tag_id=t_approved.id),
                    MaterialTag(material_id=mats[0].id, tag_id=t_alias.id),
                    MaterialTag(material_id=mats[0].id, tag_id=t_pending.id),
                    MaterialTag(material_id=mats[0].id, tag_id=t_cat_dup.id),
                ]
            )

            client = Client(display_name="Acme Studio", context_line="Video agency")
            s.add(client)
            await s.flush()
            s.add(MaterialClientLink(material_id=mats[0].id, client_id=client.id, relation="reference"))
            await s.commit()

            data = await build_graph(s, include_semantic=True)

        # 1. No category or tag physical hub nodes
        assert {n["type"] for n in data["nodes"]} == {"material", "client"}

        # 2. No belongs / category / tag / cooccur / duplicate tool edges
        reasons = {e["reason"] for e in data["edges"]}
        assert reasons.isdisjoint({"belongs", "category", "tag", "cooccur", "tool"})
        assert data["edges"] == data["links"]

        # 3. Pending tags & category duplicates stripped; design normalized to Дизайн
        n0 = next(n for n in data["nodes"] if n["id"] == mats[0].id)
        assert "PendingNoiseTag" not in n0["tags"]
        assert "искусственный интеллект" not in n0["tags"]
        assert "AI" not in n0["tags"]
        assert "Дизайн" in n0["tags"]
        assert "Claude" in n0["tags"]

        # 4. Generic link hosts skipped; rare host kept between the 2 marketing materials
        link_edges = [e for e in data["edges"] if e["reason"] == "link"]
        assert len(link_edges) == 1
        assert link_edges[0]["label"] == "rare-analytics-tool.example"

        # 5. Hard degree cap <= 5 on material<->material edges
        mat_deg: dict[str, int] = {}
        for e in data["edges"]:
            if e["reason"] != "linked":
                mat_deg[e["source"]] = mat_deg.get(e["source"], 0) + 1
                mat_deg[e["target"]] = mat_deg.get(e["target"], 0) + 1
        assert max(mat_deg.values(), default=0) <= 5

        # 6. Client node has finite 2D coordinates & linked edge
        client_node = next(n for n in data["nodes"] if n["type"] == "client")
        assert math.isfinite(client_node["x"]) and math.isfinite(client_node["y"])
        assert any(e["reason"] == "linked" and e["target"] == client_node["id"] for e in data["edges"])

        # 7. Meta & facets populated accurately
        meta = data["meta"]
        assert meta["total_materials"] == 15
        assert meta["uncategorized_count"] == 1
        assert meta["total_clusters"] == len(data["clusters"])
        assert any(c["id"] == "cluster:uncategorized" and c["count"] == 1 for c in data["clusters"])
        assert any(f["label"] == "AI" and f["count"] == 12 for f in meta["facets"]["categories"])
        assert all(f["label"] != "PendingNoiseTag" for f in meta["facets"]["tags"])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_large_category_auto_splits_deterministically_with_hulls_and_non_overlap(tmp_path: Path):
    engine, Session = await _make_session_factory(tmp_path, "atlas_split.db")
    try:
        async with Session() as s:
            cat_ai = Category(name="AI")
            cat_design = Category(name="Дизайн")
            s.add_all([cat_ai, cat_design])
            await s.flush()

            t_claude = Tag(name="Claude", status=TagStatus.APPROVED.value)
            t_video = Tag(name="AI монтаж", status=TagStatus.APPROVED.value)
            t_design = Tag(name="design", status=TagStatus.APPROVED.value)
            s.add_all([t_claude, t_video, t_design])
            await s.flush()

            specs = [
                ("Claude Code agent skill MCP automation", ["Claude Code", "MCP", "Cursor"], t_claude),
                ("AI video montage animation reels Kling Veo", ["Kling", "Veo 3", "Higgsfield", "CapCut"], t_video),
                ("AI UI design landing page Figma WebGL visual", ["Figma", "Midjourney", "Three.js"], t_design),
            ]
            for idx in range(36):
                topic_txt, tools, tag_obj = specs[idx % 3]
                m = Material(
                    source_type="instagram",
                    title=f"{topic_txt} case {idx}",
                    summary=f"Detailed breakdown of {topic_txt} workflow step {idx}",
                    category_id=cat_ai.id,
                    tools=tools,
                    usefulness_score=6 + (idx % 5),
                    processing_status=ProcessingStatus.DONE.value,
                    embedding={"model": LOCAL_EMBED_MODEL, "vec": local_embed_text(f"{topic_txt} {idx}")},
                )
                s.add(m)
                await s.flush()
                s.add(MaterialTag(material_id=m.id, tag_id=tag_obj.id))

            for idx in range(5):
                m = Material(
                    source_type="web",
                    title=f"Typography grid poster {idx}",
                    summary="Editorial layout systems and color palette design",
                    category_id=cat_design.id,
                    tools=["Figma"],
                    usefulness_score=8,
                    processing_status=ProcessingStatus.DONE.value,
                    embedding={"model": LOCAL_EMBED_MODEL, "vec": local_embed_text(f"Typography grid poster {idx}")},
                )
                s.add(m)

            await s.commit()

            g1 = await build_graph(s, include_semantic=True)
            invalidate_graph_cache()
            g2 = await build_graph(s, include_semantic=True)

        ai_clusters = [c for c in g1["clusters"] if c["category"] == "AI"]
        assert 2 <= len(ai_clusters) <= 4, f"Expected AI to split into 2-4 subclusters, got {ai_clusters}"
        assert sum(c["count"] for c in ai_clusters) == 36
        labels = [c["label"] for c in g1["clusters"]]
        assert len(labels) == len(set(labels))
        for c in g1["clusters"]:
            assert c["radius"] > 0
            assert len(c["hull"]) >= 3

        coords1 = {n["id"]: (n["x"], n["y"]) for n in g1["nodes"]}
        coords2 = {n["id"]: (n["x"], n["y"]) for n in g2["nodes"]}
        assert coords1 == coords2

        cls = g1["clusters"]
        for i in range(len(cls)):
            for j in range(i + 1, len(cls)):
                dist = math.hypot(cls[i]["x"] - cls[j]["x"], cls[i]["y"] - cls[j]["y"])
                assert dist >= cls[i]["radius"] + cls[j]["radius"] - 1.0

        cluster_ids = {c["id"] for c in cls}
        mat_nodes = [n for n in g1["nodes"] if n["type"] == "material"]
        assert all(n["cluster_id"] in cluster_ids for n in mat_nodes)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_api_graph_endpoint_filters_and_light_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WEB_API_TOKEN", "atlas-test-token")
    monkeypatch.setenv("ALLOW_PLACEHOLDER_SECRETS", "false")
    get_settings.cache_clear()

    engine, Session = await _make_session_factory(tmp_path, "atlas_api.db")

    async def override_session():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    try:
        async with Session() as s:
            cat_ai = Category(name="AI")
            cat_sales = Category(name="Продажи")
            s.add_all([cat_ai, cat_sales])
            await s.flush()

            t_claude = Tag(name="Claude", status=TagStatus.APPROVED.value)
            s.add(t_claude)
            await s.flush()

            m1 = Material(
                source_type="instagram",
                source_url="https://instagram.com/reel/1",
                title="Claude Code hooks tutorial",
                summary="How to build hooks in Cursor and Claude",
                category_id=cat_ai.id,
                tools=["Claude Code"],
                usefulness_score=9,
                processing_status=ProcessingStatus.DONE.value,
            )
            m2 = Material(
                source_type="youtube",
                source_url="https://youtube.com/watch?v=2",
                title="Cold outreach B2B scripts",
                summary="Sales conversion scripts",
                category_id=cat_sales.id,
                tools=["CRM"],
                usefulness_score=6,
                processing_status=ProcessingStatus.DONE.value,
            )
            s.add_all([m1, m2])
            await s.flush()
            s.add(MaterialTag(material_id=m1.id, tag_id=t_claude.id))
            await s.commit()

        headers = {"Authorization": "Bearer atlas-test-token"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            r_full = await client.get("/api/graph")
            assert r_full.status_code == 200
            body = r_full.json()
            assert body["meta"]["total_materials"] == 2
            assert len(body["clusters"]) == 2
            assert body["edges"] == body["links"]

            r_cat = await client.get("/api/graph", params={"category": "AI"})
            assert r_cat.status_code == 200
            b_cat = r_cat.json()
            assert b_cat["meta"]["total_materials"] == 1
            assert b_cat["nodes"][0]["title"] == "Claude Code hooks tutorial"
            assert len(b_cat["meta"]["facets"]["categories"]) == 2

            r_q = await client.get("/api/graph", params={"tag": "Claude", "min_score": 8, "q": "hooks"})
            assert r_q.status_code == 200
            assert r_q.json()["meta"]["total_materials"] == 1

            r_tool = await client.get("/api/graph", params={"tool": "CRM"})
            assert r_tool.status_code == 200
            assert r_tool.json()["meta"]["total_materials"] == 1
            assert r_tool.json()["nodes"][0]["category"] == "Продажи"

            r_light = await client.get("/api/graph", params={"light": "true"})
            assert r_light.status_code == 200
            b_light = r_light.json()
            assert len(b_light["nodes"]) == 2
            assert b_light["edges"] == []
            assert b_light["clusters"] == []
    finally:
        app.dependency_overrides.pop(get_session, None)
        get_settings.cache_clear()
        await engine.dispose()
