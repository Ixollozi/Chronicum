"""Knowledge graph: topic hubs + material links (Obsidian-like local expand)."""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Material, MaterialTag, ProcessingStatus

# Same idea, different spelling → one hub (less duplicate spokes / crossings)
from app.services.taxonomy import TAG_DISPLAY, normalize_category_key, normalize_key as _norm_tag_key

_SKIP_LINK_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "t.me",
    "google.com",
    "www.google.com",
}
_MAX_SHARED_EDGES = 180
_MAX_SHARED_DEGREE = 6


def _topic_id(kind: str, name: str) -> str:
    if kind == "tag":
        return f"tag:{_norm_tag_key(name)}"
    return f"{kind}:{normalize_category_key(name) or name.lower()}"


def _tag_title(name: str) -> str:
    key = _norm_tag_key(name)
    return TAG_DISPLAY.get(key, name.strip())


def _link_host(item: object) -> str:
    if isinstance(item, dict):
        url = str(item.get("url") or item.get("href") or "")
    elif isinstance(item, str):
        url = item
    else:
        return ""
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""
    if not host or "." not in host or host in _SKIP_LINK_HOSTS:
        return ""
    return host


def _add_material_shared_edges(
    *,
    tool_materials: dict[str, set[str]],
    link_materials: dict[str, set[str]],
    links: list[dict],
    touch,
) -> None:
    """Connect materials that share a tool name or link host (archive map, not semantic KG)."""
    candidates: list[tuple[int, str, str, str]] = []  # weight, a, b, reason
    seen_pairs: set[tuple[str, str]] = set()

    def consider(bucket: dict[str, set[str]], reason: str) -> None:
        for _key, mids in bucket.items():
            if len(mids) < 2:
                continue
            ordered = sorted(mids)
            hub = ordered[0]
            for other in ordered[1 : min(len(ordered), 8)]:
                a, b = (hub, other) if hub < other else (other, hub)
                pair = (a, b)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidates.append((len(mids), a, b, reason))
            for i in range(1, min(len(ordered) - 1, 6)):
                a, b = ordered[i], ordered[i + 1]
                pair = (a, b) if a < b else (b, a)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidates.append((len(mids), pair[0], pair[1], reason))

    consider(tool_materials, "tool")
    consider(link_materials, "link")
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

    degree_shared: dict[str, int] = defaultdict(int)
    added = 0
    for w, a, b, reason in candidates:
        if added >= _MAX_SHARED_EDGES:
            break
        if degree_shared[a] >= _MAX_SHARED_DEGREE or degree_shared[b] >= _MAX_SHARED_DEGREE:
            continue
        links.append({"source": a, "target": b, "reason": reason, "weight": w})
        touch(a, b)
        degree_shared[a] += 1
        degree_shared[b] += 1
        added += 1


async def build_graph(
    session: AsyncSession,
    *,
    limit: int = 300,
    user_id: str | None = None,
    scope_user: bool = False,
) -> dict:
    where = [
        Material.processing_status.in_(
            [
                ProcessingStatus.DONE.value,
                ProcessingStatus.AWAITING_FULL.value,
                ProcessingStatus.PREVIEW.value,
            ]
        )
    ]
    if scope_user:
        where.append(Material.user_id == user_id if user_id else Material.user_id.is_(None))
    rows = (
        await session.execute(
            select(Material)
            .options(
                selectinload(Material.category),
                selectinload(Material.tags).selectinload(MaterialTag.tag),
            )
            .where(*where)
            .order_by(Material.created_at.desc())
            .limit(limit)
        )
    ).scalars().unique().all()

    nodes_by_id: dict[str, dict] = {}
    links: list[dict] = []
    degree: dict[str, int] = defaultdict(int)
    topic_materials: dict[str, set[str]] = defaultdict(set)
    belongs: dict[tuple[str, str], int] = defaultdict(int)
    cat_bridge: dict[tuple[str, str], int] = defaultdict(int)
    tool_materials: dict[str, set[str]] = defaultdict(set)
    link_materials: dict[str, set[str]] = defaultdict(set)

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

        for tool in m.tools or []:
            key = str(tool or "").strip().lower()
            if len(key) >= 2:
                tool_materials[key].add(mid)
        for item in m.links or []:
            host = _link_host(item)
            if host:
                link_materials[host].add(mid)

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
                belongs[(cat_id, tid)] += 1

    for (cat_id, tag_id), w in belongs.items():
        links.append({"source": cat_id, "target": tag_id, "reason": "belongs", "weight": w})
        touch(cat_id, tag_id)

    tag_ids = [tid for tid in topic_materials if tid.startswith("tag:")]
    orphans = [tid for tid in tag_ids if not any(c for (c, t) in belongs if t == tid)]
    if len(orphans) > 1:
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

    _ = cat_bridge

    _add_material_shared_edges(
        tool_materials=tool_materials,
        link_materials=link_materials,
        links=links,
        touch=touch,
    )

    # Client nodes ONLY via explicit confirmed links (never name co-occurrence)
    try:
        from app.models import Client, MaterialClientLink

        link_rows = (
            await session.execute(
                select(MaterialClientLink, Client)
                .join(Client, Client.id == MaterialClientLink.client_id)
                .where(MaterialClientLink.material_id.in_(list(nodes_by_id.keys())))
            )
        ).all()
        for link_row, client in link_rows:
            cid = f"client:{client.id}"
            if cid not in nodes_by_id:
                nodes_by_id[cid] = {
                    "id": cid,
                    "type": "client",
                    "title": client.display_name,
                    "category": None,
                    "tags": [],
                    "score": None,
                    "status": "client",
                    "source_type": "client",
                    "summary": client.context_line,
                }
            links.append(
                {"source": link_row.material_id, "target": cid, "reason": "linked", "weight": 1}
            )
            touch(link_row.material_id, cid)
    except Exception:
        pass

    nodes = []
    for n in nodes_by_id.values():
        n["degree"] = degree.get(n["id"], 0)
        nodes.append(n)

    return {"nodes": nodes, "links": links}
