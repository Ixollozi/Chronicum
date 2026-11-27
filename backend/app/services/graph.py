"""Knowledge Atlas graph service: clean material/client nodes, IDF + mutual top-K edges,
auto-subclustered categories, and deterministic 16:9 2D coordinates.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Material, MaterialTag, ProcessingStatus, TagStatus
from app.search.embeddings import (
    LOCAL_EMBED_MODEL,
    cosine_similarity,
    local_embed_text,
    material_embedding_text,
)
from app.services.graph_atlas import (
    AtlasItem,
    build_clusters,
    compute_cluster_edges,
    layout_atlas,
)
from app.services.taxonomy import (
    GRAPH_META_TAGS,
    clean_graph_tags,
    display_category_name,
    display_tag_name,
    normalize_category_key,
    normalize_key as _norm_tag_key,
)

try:  # Optional fast vectorized cosine matrix path
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover
    np = None  # type: ignore

# Raised semantic thresholds (local hash vectors naturally have lower cosine range than dense neural models)
_SEMANTIC_EDGE_THRESHOLD_LOCAL = 0.41
_SEMANTIC_EDGE_THRESHOLD_DENSE = 0.55

# Hard degree caps per material (strictly eliminates hairball hubs)
_MAX_MATERIAL_DEGREE = 5
_MAX_TOOL_CANDIDATES_PER_MAT = 2
_MAX_SEMANTIC_CANDIDATES_PER_MAT = 3
_MAX_LINK_CANDIDATES_PER_MAT = 1
_MAX_LINK_DOMAIN_DF = 12

_DEFAULT_GRAPH_LIMIT = 1500
_GRAPH_CACHE_TTL_SECONDS = 30.0
_graph_cache: dict[tuple, tuple[float, dict]] = {}

# Generic hosting/social/platform domains that must NOT glue unrelated materials together
_SKIP_LINK_HOSTS: frozenset[str] = frozenset(
    {
        "instagram.com",
        "www.instagram.com",
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "t.me",
        "telegram.me",
        "google.com",
        "www.google.com",
        "docs.google.com",
        "drive.google.com",
        "github.com",
        "gist.github.com",
        "raw.githubusercontent.com",
        "x.com",
        "twitter.com",
        "notion.so",
        "notion.site",
        "figma.com",
        "vercel.app",
        "vercel.com",
        "netlify.app",
        "reddit.com",
        "medium.com",
        "substack.com",
        "linkedin.com",
        "facebook.com",
        "tiktok.com",
        "vk.com",
        "yandex.ru",
        "dzen.ru",
        "habr.com",
        "vc.ru",
        "huggingface.co",
        "npmjs.com",
        "pypi.org",
        "bit.ly",
        "tinyurl.com",
        "goo.gl",
        "linktr.ee",
        "taplink.cc",
        "openai.com",
        "anthropic.com",
        "claude.ai",
        "chatgpt.com",
    }
)

# Ultra-generic tool strings that carry no specific material-to-material signal
_GENERIC_TOOL_KEYS: frozenset[str] = frozenset(
    {
        "ai",
        "ии",
        "нейросети",
        "нейросеть",
        "нейронные сети",
        "neural networks",
        "artificial intelligence",
        "github",
        "google",
        "youtube",
        "instagram",
        "telegram",
        "tiktok",
        "reels",
        "browser",
        "браузер",
        "сайт",
        "интернет",
        "api",
        "web",
    }
)


def invalidate_graph_cache() -> None:
    _graph_cache.clear()


def _cache_key(
    session: AsyncSession,
    limit: int,
    mode: str,
    scope_user: bool = False,
    user_id: str | None = None,
    filters: tuple = (),
) -> tuple:
    bind_id = id(session.get_bind()) if session is not None else 0
    return (bind_id, limit, mode, scope_user, user_id, filters)


def _material_entities(m: Material) -> dict[str, str]:
    """Return `{normalized_key: display_label}` for tools & technologies on a material."""
    out: dict[str, str] = {}
    for field in (m.tools or [], m.technologies or []):
        if not isinstance(field, list):
            continue
        for raw in field:
            label = str(raw or "").strip()
            if len(label) < 2:
                continue
            key = _norm_tag_key(label)
            if len(key) < 2 or key in _GENERIC_TOOL_KEYS:
                continue
            # Filter obvious OCR keyboard/shortcut noise like "F7 F8 F9 F10"
            if all(tok.startswith("f") and tok[1:].isdigit() for tok in key.split()):
                continue
            if key not in out:
                out[key] = display_tag_name(label, key) if key != label.lower() else label
    return out


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
    # Also skip subdomains of generic app hosts (e.g. *.vercel.app, *.netlify.app, *.notion.site)
    for suffix in (".vercel.app", ".netlify.app", ".notion.site", ".github.io"):
        if host.endswith(suffix):
            return ""
    return host


def _approved_material_tags(m: Material, category_name: str | None) -> list[str]:
    """Approved, normalized, deduplicated tags on a material (excludes pending/rejected & category dupes)."""
    raw_approved = [
        mt.tag.name
        for mt in (m.tags or [])
        if getattr(mt, "tag", None) is not None
        and getattr(mt.tag, "status", None) == TagStatus.APPROVED.value
    ]
    return clean_graph_tags(raw_approved, category_name=category_name, include_meta=True)


def _build_facets(rows: list[Material]) -> dict[str, list[dict[str, Any]]]:
    """Compute filter facets (categories, approved tags, tools, source_types) across materials."""
    cat_counts: Counter[str] = Counter()
    cat_labels: dict[str, str] = {}
    tag_counts: Counter[str] = Counter()
    tag_labels: dict[str, str] = {}
    tool_counts: Counter[str] = Counter()
    tool_labels: dict[str, str] = {}
    source_counts: Counter[str] = Counter()

    for m in rows:
        raw_cat = m.category.name if m.category else None
        ck = normalize_category_key(raw_cat or "")
        if ck:
            cat_counts[ck] += 1
            cat_labels.setdefault(ck, display_category_name(raw_cat or ck, ck))
        else:
            cat_counts["__uncategorized__"] += 1
            cat_labels.setdefault("__uncategorized__", "Неразобранное")

        for t in _approved_material_tags(m, raw_cat):
            tk = _norm_tag_key(t)
            if tk:
                tag_counts[tk] += 1
                tag_labels.setdefault(tk, display_tag_name(t, tk))

        for ek, lbl in _material_entities(m).items():
            tool_counts[ek] += 1
            tool_labels.setdefault(ek, lbl)

        st = (m.source_type or "other").strip().lower()
        source_counts[st] += 1

    def _fmt(counts: Counter[str], labels: dict[str, str], limit_n: int) -> list[dict[str, Any]]:
        ordered = sorted(
            counts.items(),
            key=lambda kv: (1 if kv[0] == "__uncategorized__" else 0, -kv[1], labels.get(kv[0], kv[0]).casefold()),
        )
        return [{"key": k, "label": labels.get(k, k), "count": cnt} for k, cnt in ordered[:limit_n]]

    return {
        "categories": _fmt(cat_counts, cat_labels, 50),
        "tags": _fmt(tag_counts, tag_labels, 60),
        "tools": _fmt(tool_counts, tool_labels, 60),
        "source_types": [
            {"key": k, "label": k, "count": cnt}
            for k, cnt in sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


def _matches_filters(
    m: Material,
    *,
    cat_filter: str | None,
    tag_filter: str | None,
    tool_filter: str | None,
    q_filter: str | None,
    min_score: int | None,
    source_type: str | None,
) -> bool:
    raw_cat = m.category.name if m.category else None
    ck = normalize_category_key(raw_cat or "")

    if cat_filter is not None:
        if cat_filter in {"__uncategorized__", "uncategorized", "без категории", "неразобранное"}:
            if ck:
                return False
        else:
            if ck != normalize_category_key(cat_filter):
                return False

    if min_score is not None:
        if (m.usefulness_score or 0) < min_score:
            return False

    if source_type is not None:
        if (m.source_type or "").strip().lower() != source_type.strip().lower():
            return False

    if tag_filter is not None:
        target_tk = _norm_tag_key(tag_filter)
        mat_tags = {_norm_tag_key(t) for t in _approved_material_tags(m, raw_cat)}
        if target_tk not in mat_tags:
            return False

    if tool_filter is not None:
        target_tool = _norm_tag_key(tool_filter)
        mat_tools = set(_material_entities(m).keys())
        if target_tool not in mat_tools:
            return False

    if q_filter:
        ql = q_filter.strip().lower()
        hay = " ".join(
            [
                m.title or "",
                m.summary or "",
                raw_cat or "",
                " ".join(_approved_material_tags(m, raw_cat)),
                " ".join(_material_entities(m).values()),
            ]
        ).lower()
        if ql not in hay:
            return False

    return True


def _compute_shared_tool_candidates(
    entities_by_mid: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """IDF-weighted shared tool/technology edges, mutual top-K prioritized."""
    n_mats = len(entities_by_mid)
    if n_mats < 2:
        return []

    entity_index: dict[str, list[str]] = defaultdict(list)
    for mid, ents in entities_by_mid.items():
        for ek in ents:
            entity_index[ek].append(mid)

    # Skip ubiquitous tools (e.g. df > 25% of archive when N >= 15) that glue everything together
    max_df = max(20, int(round(n_mats * 0.25))) if n_mats >= 15 else n_mats
    shared_pairs: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)

    for ek, mids in entity_index.items():
        df = len(mids)
        if df < 2 or df > max_df:
            continue
        idf = math.log((n_mats + 1.0) / (df + 1.0)) + 0.5
        ordered = sorted(mids)
        for i in range(len(ordered)):
            a = ordered[i]
            lbl = entities_by_mid[a].get(ek, ek)
            for j in range(i + 1, len(ordered)):
                b = ordered[j]
                shared_pairs[(a, b)].append((lbl, idf))

    neighbors: dict[str, list[tuple[float, int, str, list[str]]]] = defaultdict(list)
    for (a, b), items in shared_pairs.items():
        items.sort(key=lambda x: (-x[1], x[0]))
        labels = [lbl for lbl, _ in items]
        idf_sum = sum(val for _, val in items)
        neighbors[a].append((idf_sum, len(labels), b, labels))
        neighbors[b].append((idf_sum, len(labels), a, labels))

    top_sets: dict[str, set[str]] = {}
    for mid, cands in neighbors.items():
        cands.sort(key=lambda x: (-x[0], -x[1], x[2]))
        top_sets[mid] = {other for _, _, other, _ in cands[:_MAX_TOOL_CANDIDATES_PER_MAT]}

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for mid, cands in neighbors.items():
        for idf_sum, cnt, other, labels in cands[:_MAX_TOOL_CANDIDATES_PER_MAT]:
            pair = (mid, other) if mid < other else (other, mid)
            if pair in seen:
                continue
            seen.add(pair)
            is_mutual = other in top_sets.get(mid, set()) and mid in top_sets.get(other, set())
            out.append(
                {
                    "source": pair[0],
                    "target": pair[1],
                    "reason": "shared_tool",
                    "weight": cnt,
                    "label": ", ".join(labels[:3]),
                    "_score": idf_sum,
                    "_mutual": is_mutual,
                }
            )
    return out


def _compute_link_candidates(link_materials: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Connect materials sharing rare, non-generic domains (2 <= df <= _MAX_LINK_DOMAIN_DF)."""
    pair_hosts: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for host, mids in link_materials.items():
        df = len(mids)
        if df < 2 or df > _MAX_LINK_DOMAIN_DF:
            continue
        ordered = sorted(mids)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                pair_hosts[(ordered[i], ordered[j])].append((host, df))

    neighbors: dict[str, list[tuple[float, str, list[str]]]] = defaultdict(list)
    for (a, b), items in pair_hosts.items():
        items.sort(key=lambda x: (x[1], x[0]))
        rarity = sum(1.0 / max(1, df) for _, df in items)
        hosts = [h for h, _ in items]
        neighbors[a].append((rarity, b, hosts))
        neighbors[b].append((rarity, a, hosts))

    top_sets: dict[str, set[str]] = {}
    for mid, cands in neighbors.items():
        cands.sort(key=lambda x: (-x[0], x[1]))
        top_sets[mid] = {other for _, other, _ in cands[:_MAX_LINK_CANDIDATES_PER_MAT]}

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for mid, cands in neighbors.items():
        for rarity, other, hosts in cands[:_MAX_LINK_CANDIDATES_PER_MAT]:
            pair = (mid, other) if mid < other else (other, mid)
            if pair in seen:
                continue
            seen.add(pair)
            is_mutual = other in top_sets.get(mid, set()) and mid in top_sets.get(other, set())
            out.append(
                {
                    "source": pair[0],
                    "target": pair[1],
                    "reason": "link",
                    "weight": len(hosts),
                    "label": ", ".join(hosts[:2]),
                    "_score": rarity,
                    "_mutual": is_mutual,
                }
            )
    return out


def _prepare_vectors(
    mat_inputs: list[tuple[str, dict | None, str]],
) -> tuple[dict[str, tuple[str, list[float]]], dict[str, list[float]]]:
    """Extract normalized vectors per material + local fallback vectors."""
    vec_by_mid: dict[str, tuple[str, list[float]]] = {}
    local_by_mid: dict[str, list[float]] = {}
    for mid, emb, emb_text in mat_inputs:
        if isinstance(emb, dict) and emb.get("model") and isinstance(emb.get("vec"), list) and emb["vec"]:
            model_name = str(emb["model"])
            vec_by_mid[mid] = (model_name, emb["vec"])
            if model_name == LOCAL_EMBED_MODEL:
                local_by_mid[mid] = emb["vec"]
            elif emb_text:
                lv = local_embed_text(emb_text)
                if lv:
                    local_by_mid[mid] = lv
        elif emb_text:
            lv = local_embed_text(emb_text)
            if lv:
                vec_by_mid[mid] = (LOCAL_EMBED_MODEL, lv)
                local_by_mid[mid] = lv
    return vec_by_mid, local_by_mid


def _compute_semantic_candidates(
    vec_by_mid: dict[str, tuple[str, list[float]]],
    local_by_mid: dict[str, list[float]],
) -> list[dict[str, Any]]:
    """Vectorized semantic cosine top-K with raised threshold & mutual-kNN boost."""
    mids = sorted(vec_by_mid.keys())
    n = len(mids)
    if n < 2:
        return []

    sem_neighbors: dict[str, list[tuple[float, str]]] = defaultdict(list)

    # Fast vectorized path when numpy is available and all items share one model + dim
    models = {vec_by_mid[m][0] for m in mids}
    dims = {len(vec_by_mid[m][1]) for m in mids}
    if np is not None and len(models) == 1 and len(dims) == 1:
        model_name = next(iter(models))
        thr = _SEMANTIC_EDGE_THRESHOLD_LOCAL if model_name == LOCAL_EMBED_MODEL else _SEMANTIC_EDGE_THRESHOLD_DENSE
        M = np.asarray([vec_by_mid[m][1] for m in mids], dtype=np.float32)
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        norms = np.where(norms > 1e-12, norms, 1.0)
        M = M / norms
        S = M @ M.T
        np.fill_diagonal(S, -1.0)
        # Extract top candidates above threshold per row
        for i in range(n):
            row = S[i]
            hits = np.flatnonzero(row >= thr)
            if hits.size == 0:
                continue
            a = mids[i]
            for j in hits:
                if j > i:
                    sim = float(row[j])
                    b = mids[int(j)]
                    sem_neighbors[a].append((sim, b))
                    sem_neighbors[b].append((sim, a))
    else:
        for i in range(n):
            a = mids[i]
            model_a, vec_a = vec_by_mid[a]
            for j in range(i + 1, n):
                b = mids[j]
                model_b, vec_b = vec_by_mid[b]
                if model_a == model_b:
                    sim = cosine_similarity(vec_a, vec_b)
                    thr = _SEMANTIC_EDGE_THRESHOLD_LOCAL if model_a == LOCAL_EMBED_MODEL else _SEMANTIC_EDGE_THRESHOLD_DENSE
                else:
                    la = local_by_mid.get(a)
                    lb = local_by_mid.get(b)
                    if not la or not lb:
                        continue
                    sim = cosine_similarity(la, lb)
                    thr = _SEMANTIC_EDGE_THRESHOLD_LOCAL
                if sim >= thr:
                    sem_neighbors[a].append((sim, b))
                    sem_neighbors[b].append((sim, a))

    top_sets: dict[str, set[str]] = {}
    for mid, cands in sem_neighbors.items():
        cands.sort(key=lambda x: (-x[0], x[1]))
        top_sets[mid] = {other for _, other in cands[:_MAX_SEMANTIC_CANDIDATES_PER_MAT]}

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for mid, cands in sem_neighbors.items():
        for sim, other in cands[:_MAX_SEMANTIC_CANDIDATES_PER_MAT]:
            pair = (mid, other) if mid < other else (other, mid)
            if pair in seen:
                continue
            seen.add(pair)
            is_mutual = other in top_sets.get(mid, set()) and mid in top_sets.get(other, set())
            out.append(
                {
                    "source": pair[0],
                    "target": pair[1],
                    "reason": "semantic",
                    "weight": max(1, int(round(sim * 100))),
                    "similarity": round(sim, 3),
                    "_score": sim,
                    "_mutual": is_mutual,
                }
            )
    return out


def _select_capped_material_edges(
    *,
    tool_cands: list[dict[str, Any]],
    sem_cands: list[dict[str, Any]],
    link_cands: list[dict[str, Any]],
    max_degree: int = _MAX_MATERIAL_DEGREE,
) -> list[dict[str, Any]]:
    """Select clean material↔material edges respecting mutual top-K and strict per-node degree cap."""
    # Sort each pool: mutual pairs first, then descending strength, deterministic tie-break
    def _sort_pool(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            pool,
            key=lambda e: (0 if e.get("_mutual") else 1, -float(e.get("_score") or 0.0), e["source"], e["target"]),
        )

    deg_total: Counter[str] = Counter()
    deg_by_reason: dict[str, Counter[str]] = defaultdict(Counter)
    reason_caps = {
        "semantic": 3,
        "shared_tool": 2,
        "link": 1,
    }

    selected: list[dict[str, Any]] = []
    seen_pair_reason: set[tuple[str, str, str]] = set()

    # Interleave semantic + shared_tool + link so every node gets balanced high-confidence links
    # within the strict total cap max_degree <= 5.
    ordered_pools = [
        _sort_pool(sem_cands),
        _sort_pool(tool_cands),
        _sort_pool(link_cands),
    ]
    # Pass 1: mutual edges across all pools, then Pass 2: strong non-mutual edges
    for mutual_only in (True, False):
        for pool in ordered_pools:
            for e in pool:
                if mutual_only and not e.get("_mutual"):
                    continue
                if not mutual_only and e.get("_mutual"):
                    continue
                a, b, reason = e["source"], e["target"], e["reason"]
                key = (a, b, reason)
                if key in seen_pair_reason:
                    continue
                rcap = reason_caps.get(reason, 2)
                if deg_total[a] >= max_degree or deg_total[b] >= max_degree:
                    continue
                if deg_by_reason[reason][a] >= rcap or deg_by_reason[reason][b] >= rcap:
                    continue
                seen_pair_reason.add(key)
                deg_total[a] += 1
                deg_total[b] += 1
                deg_by_reason[reason][a] += 1
                deg_by_reason[reason][b] += 1
                clean_edge = {k: v for k, v in e.items() if not k.startswith("_")}
                selected.append(clean_edge)

    return selected


async def build_graph(
    session: AsyncSession,
    *,
    limit: int = _DEFAULT_GRAPH_LIMIT,
    include_semantic: bool = True,
    user_id: str | None = None,
    scope_user: bool = False,
    category: str | None = None,
    tag: str | None = None,
    tool: str | None = None,
    q: str | None = None,
    min_score: int | None = None,
    source_type: str | None = None,
) -> dict:
    mode = "full" if include_semantic else "light"
    filter_tuple = (
        (category or "").strip().lower(),
        (tag or "").strip().lower(),
        (tool or "").strip().lower(),
        (q or "").strip().lower(),
        min_score,
        (source_type or "").strip().lower(),
    )
    ckey = _cache_key(session, limit, mode, scope_user, user_id, filter_tuple)
    cached = _graph_cache.get(ckey)
    now = time.monotonic()
    if cached and (now - cached[0]) < _GRAPH_CACHE_TTL_SECONDS:
        return cached[1]

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
    all_rows = (
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

    facets = _build_facets(all_rows)
    has_filters = any(v not in (None, "") for v in (category, tag, tool, q, min_score, source_type))
    rows = (
        [
            m
            for m in all_rows
            if _matches_filters(
                m,
                cat_filter=category,
                tag_filter=tag,
                tool_filter=tool,
                q_filter=q,
                min_score=min_score,
                source_type=source_type,
            )
        ]
        if has_filters
        else all_rows
    )

    uncategorized_count = sum(1 for m in rows if not m.category or not normalize_category_key(m.category.name))
    untagged_count = 0

    nodes_by_id: dict[str, dict[str, Any]] = {}
    entities_by_mid: dict[str, dict[str, str]] = {}
    link_materials: dict[str, set[str]] = defaultdict(set)

    for m in rows:
        raw_cat = m.category.name if m.category else None
        ck = normalize_category_key(raw_cat or "")
        cat_label = display_category_name(raw_cat or ck, ck) if ck else None
        approved_tags = _approved_material_tags(m, cat_label)
        if not approved_tags:
            untagged_count += 1
        ents = _material_entities(m)
        entities_by_mid[m.id] = ents
        top_tools = list(ents.values())[:6]

        for item in m.links or []:
            host = _link_host(item)
            if host:
                link_materials[host].add(m.id)

        created_iso = m.created_at.isoformat() if getattr(m, "created_at", None) else None
        nodes_by_id[m.id] = {
            "id": m.id,
            "type": "material",
            "title": m.title or "Без названия",
            "category": cat_label,
            "cluster_id": None,
            "tags": approved_tags,
            "tools": top_tools,
            "score": m.usefulness_score,
            "status": m.processing_status,
            "source_type": m.source_type,
            "url": m.source_url,
            "summary": (m.summary or "")[:280] or None,
            "created_at": created_iso,
            "x": 0.0,
            "y": 0.0,
            "degree": 0,
        }

    # Fast light mode (used by homepage search autocomplete): skip semantic/clustering/layout
    if not include_semantic:
        nodes = list(nodes_by_id.values())
        payload = {
            "clusters": [],
            "nodes": nodes,
            "edges": [],
            "links": [],
            "cluster_edges": [],
            "meta": {
                "total_materials": len(rows),
                "total_nodes": len(nodes),
                "total_edges": 0,
                "total_clusters": 0,
                "uncategorized_count": uncategorized_count,
                "untagged_count": untagged_count,
                "max_degree": 0,
                "median_degree": 0.0,
                "edge_counts": {},
                "facets": facets,
            },
        }
        _graph_cache[ckey] = (time.monotonic(), payload)
        return payload

    # Client links ONLY via explicit confirmed MaterialClientLink records
    client_links: dict[str, list[str]] = defaultdict(list)
    client_edges: list[dict[str, Any]] = []
    if nodes_by_id:
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
                        "cluster_id": None,
                        "tags": [],
                        "tools": [],
                        "score": None,
                        "status": "client",
                        "source_type": "client",
                        "url": None,
                        "summary": client.context_line,
                        "created_at": client.created_at.isoformat() if getattr(client, "created_at", None) else None,
                        "x": 0.0,
                        "y": 0.0,
                        "degree": 0,
                    }
                client_links[cid].append(link_row.material_id)
                client_edges.append(
                    {
                        "source": link_row.material_id,
                        "target": cid,
                        "reason": "linked",
                        "weight": 1,
                        "label": client.display_name,
                    }
                )
        except Exception:
            pass

    # Heavy math (embeddings, cosine similarity, spherical k-means, 16:9 layout) off the event loop
    mat_inputs: list[tuple[str, dict | None, str]] = [
        (m.id, getattr(m, "embedding", None), material_embedding_text(m))
        for m in rows
    ]

    def _compute_atlas() -> tuple[list[dict], list[dict], list[dict], dict[str, str], dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
        vec_by_mid, local_by_mid = _prepare_vectors(mat_inputs)

        tool_cands = _compute_shared_tool_candidates(entities_by_mid)
        sem_cands = _compute_semantic_candidates(vec_by_mid, local_by_mid)
        link_cands = _compute_link_candidates(link_materials)

        mat_edges = _select_capped_material_edges(
            tool_cands=tool_cands,
            sem_cands=sem_cands,
            link_cands=link_cands,
            max_degree=_MAX_MATERIAL_DEGREE,
        )

        # Build AtlasItem list for clustering & 2D layout
        atlas_items: list[AtlasItem] = []
        items_by_id: dict[str, AtlasItem] = {}
        for m in rows:
            node = nodes_by_id[m.id]
            vec = local_by_mid.get(m.id) or (vec_by_mid[m.id][1] if m.id in vec_by_mid else [])
            it = AtlasItem(
                id=m.id,
                title=node["title"],
                summary=node["summary"] or "",
                category=node["category"],
                category_key=normalize_category_key(node["category"] or ""),
                tags=node["tags"],
                tools=list(entities_by_mid.get(m.id, {}).values()),
                score=node["score"],
                vec=vec,
            )
            atlas_items.append(it)
            items_by_id[m.id] = it

        clusters, node_to_cluster = build_clusters(atlas_items)
        cluster_edges = compute_cluster_edges(clusters, node_to_cluster, mat_edges)
        node_coords, client_coords = layout_atlas(clusters, items_by_id, cluster_edges, client_links)

        cluster_dicts = [
            {
                "id": c.id,
                "label": c.label,
                "category": c.category,
                "color": c.color,
                "color_slot": c.color_slot,
                "count": len(c.member_ids),
                "avg_score": c.avg_score,
                "x": c.x,
                "y": c.y,
                "radius": c.radius,
                "hull": c.hull,
                "top_tags": c.top_tags,
                "top_tools": c.top_tools,
            }
            for c in clusters
        ]
        return cluster_dicts, mat_edges, cluster_edges, node_to_cluster, node_coords, client_coords

    clusters_out, mat_edges, cluster_edges, node_to_cluster, node_coords, client_coords = (
        await asyncio.to_thread(_compute_atlas)
    )

    all_edges = mat_edges + client_edges
    degree: Counter[str] = Counter()
    edge_counts: Counter[str] = Counter()
    for e in all_edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
        edge_counts[e["reason"]] += 1

    nodes_out: list[dict[str, Any]] = []
    mat_degrees: list[int] = []
    for nid, n in nodes_by_id.items():
        n["degree"] = degree.get(nid, 0)
        if n["type"] == "material":
            n["cluster_id"] = node_to_cluster.get(nid)
            xy = node_coords.get(nid, (0.0, 0.0))
            n["x"], n["y"] = xy[0], xy[1]
            mat_degrees.append(n["degree"])
        elif n["type"] == "client":
            xy = client_coords.get(nid, (0.0, 0.0))
            n["x"], n["y"] = xy[0], xy[1]
        nodes_out.append(n)

    max_deg = max(mat_degrees) if mat_degrees else 0
    med_deg = round(float(statistics.median(mat_degrees)), 2) if mat_degrees else 0.0

    payload = {
        "clusters": clusters_out,
        "nodes": nodes_out,
        "edges": all_edges,
        "links": all_edges,
        "cluster_edges": cluster_edges,
        "meta": {
            "total_materials": len(rows),
            "total_nodes": len(nodes_out),
            "total_edges": len(all_edges),
            "total_clusters": len(clusters_out),
            "uncategorized_count": uncategorized_count,
            "untagged_count": untagged_count,
            "max_degree": max_deg,
            "median_degree": med_deg,
            "edge_counts": dict(edge_counts),
            "facets": facets,
        },
    }
    _graph_cache[ckey] = (time.monotonic(), payload)
    return payload
