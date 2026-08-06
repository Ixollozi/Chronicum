"""Knowledge graph: topic hubs + material links (Obsidian-like local expand)."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Material, MaterialTag, ProcessingStatus

# Same idea, different spelling → one hub (less duplicate spokes / crossings)
from app.services.taxonomy import TAG_DISPLAY, normalize_category_key, normalize_key as _norm_tag_key


def _topic_id(kind: str, name: str) -> str:
    if kind == "tag":
        return f"tag:{_norm_tag_key(name)}"
    return f"{kind}:{normalize_category_key(name) or name.lower()}"


def _tag_title(name: str) -> str:
    key = _norm_tag_key(name)
    return TAG_DISPLAY.get(key, name.strip())


async def build_graph(session: AsyncSession, *, limit: int = 300) -> dict:
    rows = (
        await session.execute(
            select(Material)
            .options(
                selectinload(Material.category),
                selectinload(Material.tags).selectinload(MaterialTag.tag),
            )
            .where(
                Material.processing_status.in_(
                    [
                        ProcessingStatus.DONE.value,
                        ProcessingStatus.AWAITING_FULL.value,
                        ProcessingStatus.PREVIEW.value,
                    ]
                )
            )
            .order_by(Material.created_at.desc())
            .limit(limit)
        )
    ).scalars().unique().all()

    nodes_by_id: dict[str, dict] = {}
    links: list[dict] = []
    degree: dict[str, int] = defaultdict(int)
    topic_materials: dict[str, set[str]] = defaultdict(set)
    # category ↔ tag co-occurrence weight (materials that have both)
    belongs: dict[tuple[str, str], int] = defaultdict(int)
    # category ↔ category only when a material somehow lists both (rare / future-proof)
    cat_bridge: dict[tuple[str, str], int] = defaultdict(int)

    def touch(a: str, b: str) -> None:
        degree[a] += 1
        degree[b] += 1

    def ensure_category(cat: str) -> str:
        tid = _topic_id("cat", cat)
        if tid not in nodes_by_id:
            nodes_by_id[tid] = {
                "id": tid,
                "type": "category",
                "title": cat,
                "category": cat,
                "tags": [],
                "score": None,
                "status": "topic",
                "source_type": "category",
                "summary": None,
            }
        return tid

    def ensure_tag(name: str) -> str:
        tid = _topic_id("tag", name)
        if tid not in nodes_by_id:
            nodes_by_id[tid] = {
                "id": tid,
                "type": "tag",
                "title": _tag_title(name),
                "category": None,
                "tags": [_tag_title(name)],
                "score": None,
                "status": "topic",
                "source_type": "tag",
                "summary": None,
            }
        return tid

    for m in rows:
        tag_names = [mt.tag.name for mt in (m.tags or []) if mt.tag]
        cat = m.category.name if m.category else None
        mid = m.id
        nodes_by_id[mid] = {
            "id": mid,
            "type": "material",
            "title": m.title or "Без названия",
            "category": cat,
            "tags": tag_names,
            "score": m.usefulness_score,
            "status": m.processing_status,
            "source_type": m.source_type,
            "summary": (m.summary or "")[:280] or None,
        }

        cat_id: str | None = None
        if cat:
            cat_id = ensure_category(cat)
            links.append({"source": mid, "target": cat_id, "reason": "category"})
            touch(mid, cat_id)
            topic_materials[cat_id].add(mid)

        seen_tags: set[str] = set()
        for t in tag_names:
            tid = ensure_tag(t)
            if tid in seen_tags:
                continue
            seen_tags.add(tid)
            links.append({"source": mid, "target": tid, "reason": "tag"})
            touch(mid, tid)
            topic_materials[tid].add(mid)
            if cat_id:
                a, b = (cat_id, tid) if cat_id < tid else (tid, cat_id)
                # store oriented as category→tag for readability
                belongs[(cat_id, tid)] += 1

    # Hub backbone: stars (category → its tags). No tag↔tag clique → no crossed mesh.
    for (cat_id, tag_id), w in belongs.items():
        links.append({"source": cat_id, "target": tag_id, "reason": "belongs", "weight": w})
        touch(cat_id, tag_id)

    # Orphan tags (no category on their materials): light MST-style links via strongest shared material pair
    tag_ids = [tid for tid in topic_materials if tid.startswith("tag:")]
    orphans = [tid for tid in tag_ids if not any(c for (c, t) in belongs if t == tid)]
    if len(orphans) > 1:
        # connect each orphan only to its strongest co-occurring sibling (tree-ish)
        for a in orphans:
            best_b, best_w = None, 0
            for b in orphans:
                if b <= a:
                    continue
                w = len(topic_materials[a] & topic_materials[b])
                if w > best_w:
                    best_w, best_b = w, b
            if best_b and best_w > 0:
                links.append({"source": a, "target": best_b, "reason": "cooccur", "weight": best_w})
                touch(a, best_b)

    # Categories that never share a material stay disconnected (normal). Optional bridge unused.
    _ = cat_bridge

    nodes = []
    for n in nodes_by_id.values():
        n["degree"] = degree.get(n["id"], 0)
        nodes.append(n)

    return {"nodes": nodes, "links": links}
