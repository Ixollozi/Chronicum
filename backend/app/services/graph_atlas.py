"""Knowledge Atlas clustering, sub-clustering, deterministic 2D layout, and hulls.

Pure Python + optional NumPy acceleration (no heavy ML dependencies).
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.services.taxonomy import (
    GRAPH_META_TAGS,
    clean_graph_tags,
    display_category_name,
    display_tag_name,
    normalize_category_key,
    normalize_key,
)

try:  # Optional fast linear algebra path
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover
    np = None  # type: ignore

# Threshold above which a single category auto-splits into thematic subclusters
SUBCLUSTER_SPLIT_THRESHOLD = 25
SUBCLUSTER_TARGET_SIZE = 24
SUBCLUSTER_MIN_K = 2
SUBCLUSTER_MAX_K = 5
SUBCLUSTER_MIN_MEMBER_COUNT = 5

# Chronicum Dark Archive mineral palette (no purple glow; matches map-redesign-analysis.md)
ATLAS_PALETTE: list[tuple[str, str]] = [
    ("0", "#c4a574"),  # brass — primary / accent / clients
    ("1", "#7ba892"),  # sage — AI agents / Claude / automation
    ("2", "#7496b8"),  # slate blue — programming / dev
    ("3", "#c9886b"),  # terracotta — marketing / growth / content
    ("4", "#a3ad70"),  # olive — AI video / montage / motion
    ("5", "#6ea3a8"),  # sea slate — design / UI / visuals
    ("6", "#b88292"),  # dusty rose — creative / references
    ("7", "#bfa36f"),  # ochre — business / sales / freelance
]
MUTED_COLOR_SLOT = "muted"
MUTED_COLOR_HEX = "#6c7570"

# Preferred color slot by category/theme keyword for visual stability across refreshes
_CATEGORY_SLOT_HINTS: dict[str, int] = {
    "программирование": 2,
    "маркетинг": 3,
    "дизайн": 5,
    "бизнес": 7,
    "продажи": 7,
    "фриланс": 6,
    "игры": 6,
}

# Generic tools/tokens that shouldn't dominate subcluster names or TF-IDF axes
_GENERIC_CLUSTER_TOKENS: frozenset[str] = frozenset(
    {
        "ai",
        "ии",
        "нейросеть",
        "нейросети",
        "нейросетей",
        "нейросетям",
        "искусственный",
        "интеллект",
        "github",
        "google",
        "youtube",
        "instagram",
        "telegram",
        "tiktok",
        "reels",
        "reference",
        "shorts",
        "video",
        "помощью",
        "через",
        "как",
        "для",
        "или",
        "без",
        "все",
        "это",
        "что",
        "свой",
        "новые",
        "новый",
        "бесплатные",
        "бесплатный",
        "полезные",
        "полезных",
        "проекты",
        "проектов",
        "сервисы",
        "сервисов",
        "инструменты",
        "инструментов",
        "инструмент",
        "подборка",
        "обзор",
        "способ",
        "способов",
        "создание",
        "автоматизация",
        "автоматический",
        "автоматических",
        "работа",
        "работы",
        "минут",
        "секунд",
        "2025",
        "2026",
    }
)

# Readable Russian subcluster themes matched by contrastive domain signal inside a large category.
# Each tuple: (theme_key, russian_label, preferred_palette_slot, keyword_stems)
_THEMATIC_ARCHETYPES: list[tuple[str, str, int, tuple[str, ...]]] = [
    (
        "video_motion",
        "Видео и монтаж",
        4,
        (
            "монтаж",
            "motion",
            "моушн",
            "remotion",
            "seedance",
            "capcut",
            "omniflash",
            "minimax",
            "видеомодел",
            "видео",
            "ролик",
            "аватар",
            "голос",
            "elevenlabs",
            "omnivoice",
            "озвуч",
            "flow",
            "storyboard",
            "анимац",
            "faceswap",
            "higgsfield",
            "kling",
            "sora",
            "runway",
            "veo",
        ),
    ),
    (
        "design_ui",
        "Дизайн и UI",
        5,
        (
            "дизайн",
            "design",
            "figma",
            "ui",
            "ux",
            "uiux",
            "сайт",
            "лендинг",
            "баннер",
            "вектор",
            "скетч",
            "quiver",
            "magnific",
            "taste skill",
            "impeccable",
            "вайб",
            "vibe",
            "stitch",
            "визуал",
            "логотип",
            "бренд",
            "портрет",
            "карусел",
            "canva",
            "framer",
            "webflow",
            "интерфейс",
        ),
    ),
    (
        "agents_code",
        "Claude и агенты",
        1,
        (
            "claude",
            "claude code",
            "codex",
            "cursor",
            "агент",
            "agent",
            "mcp",
            "скилл",
            "skill",
            "плагин",
            "терминал",
            "репозитор",
            "парсинг",
            "scraper",
            "браузер",
            "kitesurf",
            "openclaw",
            "ollama",
            "локал",
            "код",
            "python",
            "api",
            "n8n",
            "workflow",
            "автоматиз",
        ),
    ),
    (
        "growth_content",
        "Контент, SEO и лидген",
        0,
        (
            "seo",
            "geo",
            "лидген",
            "клиент",
            "ваканс",
            "резюме",
            "cvgen",
            "job",
            "соцсет",
            "постинг",
            "zernio",
            "подписчик",
            "контент",
            "реклам",
            "meta ads",
            "таргет",
            "продаж",
            "заработ",
            "монетиз",
            "chatgpt",
            "gemini",
            "бизнес",
            "звонк",
            "встреч",
            "cluely",
            "промпт",
        ),
    ),
]

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{2,}", re.I)


@dataclass
class AtlasItem:
    id: str
    title: str
    summary: str
    category: str | None
    category_key: str
    tags: list[str]
    tools: list[str]
    score: int | None
    vec: list[float]


@dataclass
class AtlasCluster:
    id: str
    label: str
    category: str | None
    color: str
    color_slot: str
    member_ids: list[str] = field(default_factory=list)
    avg_score: float | None = None
    x: float = 0.0
    y: float = 0.0
    radius: float = 60.0
    hull: list[list[float]] = field(default_factory=list)
    top_tags: list[str] = field(default_factory=list)
    top_tools: list[str] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-12:
        return [0.0] * len(vec)
    inv = 1.0 / norm
    return [v * inv for v in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _tokenize_text(text: str) -> list[str]:
    if not text:
        return []
    raw = text.replace("ё", "е").replace("Ё", "Е").lower()
    out: list[str] = []
    for tok in _TOKEN_RE.findall(raw):
        if len(tok) < 3 or tok in _GENERIC_CLUSTER_TOKENS:
            continue
        out.append(tok)
    return out


def _item_feature_tokens(item: AtlasItem) -> Counter[str]:
    """Weighted thematic tokens for subclustering: clean tags + tools + title/summary."""
    counts: Counter[str] = Counter()
    clean_tags = clean_graph_tags(item.tags, category_name=item.category, include_meta=False)
    for t in clean_tags:
        k = normalize_key(t)
        if k and k not in _GENERIC_CLUSTER_TOKENS:
            counts[f"tag:{k}"] += 3
            for sub in _tokenize_text(k):
                counts[f"kw:{sub}"] += 2
    for tool in item.tools:
        k = normalize_key(tool)
        if k and k not in _GENERIC_CLUSTER_TOKENS and len(k) >= 2:
            counts[f"tool:{k}"] += 3
            for sub in _tokenize_text(k):
                counts[f"kw:{sub}"] += 1
    for tok in _tokenize_text(item.title):
        counts[f"kw:{tok}"] += 2
    for tok in _tokenize_text((item.summary or "")[:240]):
        counts[f"kw:{tok}"] += 1

    # Add light archetype signal tokens so closely related Russian/English phrasing aligns
    blob = f"{item.title} {' '.join(clean_tags)} {' '.join(item.tools)} {(item.summary or '')[:200]}".lower().replace("ё", "е")
    for arch_key, _label, _slot, stems in _THEMATIC_ARCHETYPES:
        hits = sum(1 for st in stems if st in blob)
        if hits:
            counts[f"arch:{arch_key}"] += min(4, hits * 2)
    return counts


def _build_subcluster_matrix(items: list[AtlasItem]) -> list[list[float]]:
    """Combine L2-normalized embedding vectors + TF-IDF thematic features per item."""
    n = len(items)
    token_bags = [_item_feature_tokens(it) for it in items]
    df: Counter[str] = Counter()
    for bag in token_bags:
        for tok in bag:
            df[tok] += 1

    # Keep informative vocabulary (df >= 2 and not in > 80% of items)
    vocab = sorted(
        tok
        for tok, cnt in df.items()
        if cnt >= 2 and cnt <= max(2, int(n * 0.85))
    )
    tok_to_idx = {tok: i for i, tok in enumerate(vocab)}

    emb_dim = max((len(it.vec) for it in items), default=0)
    combined: list[list[float]] = []
    for it, bag in zip(items, token_bags):
        emb_part = _l2_normalize(it.vec) if len(it.vec) == emb_dim and emb_dim > 0 else [0.0] * emb_dim
        tf_part = [0.0] * len(vocab)
        for tok, cnt in bag.items():
            idx = tok_to_idx.get(tok)
            if idx is not None:
                idf = math.log((n + 1.0) / (df[tok] + 1.0)) + 1.0
                tf_part[idx] = (1.0 + math.log(cnt)) * idf
        tf_part = _l2_normalize(tf_part) if vocab else []
        # Blend: embeddings carry broad semantics; TF-IDF tags/tools/archetypes sharpen boundaries
        vec = [v * 1.0 for v in emb_part] + [v * 1.45 for v in tf_part]
        combined.append(_l2_normalize(vec))
    return combined


def _spherical_kmeans(
    vectors: list[list[float]],
    k: int,
    *,
    seed: int = 42,
    restarts: int = 4,
    max_iter: int = 25,
) -> list[int]:
    """Deterministic spherical k-means++ with cosine objective."""
    n = len(vectors)
    if k <= 1 or n <= k:
        return [i % max(1, k) for i in range(n)]

    dim = len(vectors[0]) if vectors else 0
    if dim == 0:
        return [i % k for i in range(n)]

    if np is not None:
        X = np.asarray(vectors, dtype=np.float32)
        best_labels: list[int] | None = None
        best_obj = -1e18

        for r in range(restarts):
            rng = random.Random(seed + r * 997)
            # k-means++ deterministic init using cosine distance (1 - cos)
            first = rng.randrange(n)
            centroid_indices = [first]
            for _ in range(1, k):
                chosen = X[centroid_indices]
                sims = X @ chosen.T
                max_sim = sims.max(axis=1)
                dists = np.maximum(0.0, 1.0 - max_sim) ** 2
                total = float(dists.sum())
                if total <= 1e-12:
                    nxt = next((idx for idx in range(n) if idx not in centroid_indices), 0)
                else:
                    pick = rng.random() * total
                    cum = 0.0
                    nxt = n - 1
                    for idx, d in enumerate(dists):
                        cum += float(d)
                        if cum >= pick:
                            nxt = idx
                            break
                centroid_indices.append(nxt)

            centroids = X[centroid_indices].copy()
            labels = np.zeros(n, dtype=np.int32)
            for _ in range(max_iter):
                sims = X @ centroids.T
                new_labels = sims.argmax(axis=1)
                if np.array_equal(new_labels, labels):
                    break
                labels = new_labels
                for c in range(k):
                    mask = labels == c
                    if mask.any():
                        mean = X[mask].mean(axis=0)
                        norm = float(np.linalg.norm(mean))
                        if norm > 1e-12:
                            centroids[c] = mean / norm
            final_sims = X @ centroids.T
            obj = float(final_sims[np.arange(n), labels].sum())
            if obj > best_obj:
                best_obj = obj
                best_labels = [int(v) for v in labels.tolist()]
        return best_labels or [0] * n

    # Pure-Python fallback
    best_labels_py: list[int] = [0] * n
    best_obj_py = -1e18
    for r in range(restarts):
        rng = random.Random(seed + r * 997)
        centroids_py: list[list[float]] = [list(vectors[rng.randrange(n)])]
        for _ in range(1, k):
            dists = []
            for v in vectors:
                ms = max(_dot(v, c) for c in centroids_py)
                dists.append(max(0.0, 1.0 - ms) ** 2)
            total = sum(dists)
            if total <= 1e-12:
                centroids_py.append(list(vectors[len(centroids_py) % n]))
            else:
                pick = rng.random() * total
                cum = 0.0
                chosen_v = vectors[-1]
                for v, d in zip(vectors, dists):
                    cum += d
                    if cum >= pick:
                        chosen_v = v
                        break
                centroids_py.append(list(chosen_v))

        labels_py = [0] * n
        for _ in range(max_iter):
            changed = False
            for i, v in enumerate(vectors):
                best_c = max(range(k), key=lambda c: _dot(v, centroids_py[c]))
                if best_c != labels_py[i]:
                    labels_py[i] = best_c
                    changed = True
            if not changed:
                break
            for c in range(k):
                members = [vectors[i] for i in range(n) if labels_py[i] == c]
                if members:
                    mean = [sum(col) / len(members) for col in zip(*members)]
                    centroids_py[c] = _l2_normalize(mean)
        obj_py = sum(_dot(vectors[i], centroids_py[labels_py[i]]) for i in range(n))
        if obj_py > best_obj_py:
            best_obj_py = obj_py
            best_labels_py = labels_py
    return best_labels_py


def _merge_tiny_subclusters(
    items: list[AtlasItem],
    vectors: list[list[float]],
    labels: list[int],
    min_size: int = SUBCLUSTER_MIN_MEMBER_COUNT,
) -> list[int]:
    """Fold degenerate tiny subclusters (< min_size) into their nearest healthy sibling."""
    while True:
        groups: dict[int, list[int]] = defaultdict(list)
        for i, lbl in enumerate(labels):
            groups[lbl].append(i)
        if len(groups) <= 2:
            break
        tiny = [lbl for lbl, idxs in groups.items() if len(idxs) < min_size]
        if not tiny:
            break
        healthy = [lbl for lbl in groups if lbl not in tiny]
        if not healthy:
            break

        centroids: dict[int, list[float]] = {}
        for lbl, idxs in groups.items():
            mean = [sum(col) / len(idxs) for col in zip(*(vectors[i] for i in idxs))]
            centroids[lbl] = _l2_normalize(mean)

        target_tiny = min(tiny, key=lambda l: (len(groups[l]), l))
        for idx in groups[target_tiny]:
            best_h = max(healthy, key=lambda h: _dot(vectors[idx], centroids[h]))
            labels[idx] = best_h

    # Re-index deterministically 0..m-1
    order = sorted(set(labels))
    remap = {old: new for new, old in enumerate(order)}
    return [remap[l] for l in labels]


def _pick_subcluster_labels(
    parent_label: str,
    groups: list[list[AtlasItem]],
) -> list[tuple[str, int | None]]:
    """Assign distinct, readable Russian subcluster names + optional palette slot hint."""
    n_groups = len(groups)
    all_items = [it for g in groups for it in g]
    parent_key = normalize_category_key(parent_label)

    # Global doc frequency of tools & clean tags inside parent category for contrastive lift
    global_tag_df: Counter[str] = Counter()
    global_tool_df: Counter[str] = Counter()
    for it in all_items:
        for t in set(clean_graph_tags(it.tags, category_name=it.category, include_meta=False)):
            global_tag_df[normalize_key(t)] += 1
        for tool in set(normalize_key(x) for x in it.tools if x):
            if tool and tool not in _GENERIC_CLUSTER_TOKENS:
                global_tool_df[tool] += 1

    # Score each (group, archetype) pair using lift over parent average
    arch_scores: list[dict[str, float]] = []
    global_arch_rate: dict[str, float] = {}
    for arch_key, _label, _slot, stems in _THEMATIC_ARCHETYPES:
        total_hits = 0
        for it in all_items:
            blob = f"{it.title} {' '.join(it.tags)} {' '.join(it.tools)}".lower().replace("ё", "е")
            if any(st in blob for st in stems):
                total_hits += 1
        global_arch_rate[arch_key] = max(0.05, total_hits / max(1, len(all_items)))

    for g in groups:
        g_scores: dict[str, float] = {}
        for arch_key, _label, _slot, stems in _THEMATIC_ARCHETYPES:
            hits = 0
            weighted_hits = 0.0
            for it in g:
                blob = f"{it.title} {' '.join(it.tags)} {' '.join(it.tools)}".lower().replace("ё", "е")
                h = sum(1 for st in stems if st in blob)
                if h > 0:
                    hits += 1
                    weighted_hits += min(3, h)
            rate = hits / max(1, len(g))
            lift = rate / global_arch_rate[arch_key]
            # Require meaningful presence in cluster (>= 22%) and lift
            g_scores[arch_key] = (rate * 0.65 + (weighted_hits / max(1, len(g))) * 0.2) * (0.5 + 0.5 * lift) if rate >= 0.20 else 0.0
        arch_scores.append(g_scores)

    # Greedy unique assignment of archetypes by strongest score
    assigned_arch: dict[int, tuple[str, int]] = {}
    used_arch: set[str] = set()
    arch_meta = {k: (lbl, slot) for k, lbl, slot, _ in _THEMATIC_ARCHETYPES}

    candidates: list[tuple[float, int, str]] = []
    for gi, g_scores in enumerate(arch_scores):
        for arch_key, sc in g_scores.items():
            if sc > 0.18:
                candidates.append((sc, gi, arch_key))
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    for _sc, gi, arch_key in candidates:
        if gi in assigned_arch or arch_key in used_arch:
            continue
        assigned_arch[gi] = arch_meta[arch_key]
        used_arch.add(arch_key)

    # Build final unique labels; fall back to contrastive top tag/tool for any remaining group
    results: list[tuple[str, int | None]] = []
    used_labels: set[str] = set()
    for gi, g in enumerate(groups):
        if gi in assigned_arch:
            suffix, slot_hint = assigned_arch[gi]
            label = f"{parent_label} · {suffix}"
            used_labels.add(label)
            results.append((label, slot_hint))
            continue

        # Contrastive top tag/tool fallback
        local_terms: Counter[str] = Counter()
        display_map: dict[str, str] = {}
        for it in g:
            for t in clean_graph_tags(it.tags, category_name=it.category, include_meta=False):
                k = normalize_key(t)
                if k and k != parent_key and k not in _GENERIC_CLUSTER_TOKENS:
                    local_terms[k] += 2
                    display_map.setdefault(k, display_tag_name(t, k))
            for tool in it.tools:
                k = normalize_key(tool)
                if k and k != parent_key and k not in _GENERIC_CLUSTER_TOKENS and len(k) >= 2:
                    local_terms[k] += 1
                    display_map.setdefault(k, tool.strip())

        ranked = sorted(
            local_terms.items(),
            key=lambda kv: (
                -(kv[1] / max(1, len(g))) * math.log((len(all_items) + 1.0) / (global_tag_df[kv[0]] + global_tool_df[kv[0]] + 1.0)),
                -kv[1],
                kv[0],
            ),
        )
        picks = [display_map[k] for k, _ in ranked[:2]]
        if picks:
            suffix = " и ".join(picks)
        else:
            suffix = f"Инструменты и сервисы" if f"{parent_label} · Инструменты и сервисы" not in used_labels else f"Группа {gi + 1}"
        label = f"{parent_label} · {suffix}"
        if label in used_labels:
            label = f"{parent_label} · {suffix} #{gi + 1}"
        used_labels.add(label)
        results.append((label, None))

    return results


def _top_facets_for_members(
    members: list[AtlasItem],
    *,
    category_name: str | None,
) -> tuple[list[str], list[str]]:
    tag_counts: Counter[str] = Counter()
    tag_display: dict[str, str] = {}
    tool_counts: Counter[str] = Counter()
    tool_display: dict[str, str] = {}

    for it in members:
        for t in clean_graph_tags(it.tags, category_name=category_name, include_meta=False):
            k = normalize_key(t)
            if k:
                tag_counts[k] += 1
                tag_display.setdefault(k, t)
        seen_tools: set[str] = set()
        for raw_tool in it.tools:
            k = normalize_key(raw_tool)
            if not k or k in seen_tools or k in _GENERIC_CLUSTER_TOKENS or len(k) < 2:
                continue
            seen_tools.add(k)
            tool_counts[k] += 1
            tool_display.setdefault(k, raw_tool.strip())

    top_tags = [tag_display[k] for k, _ in tag_counts.most_common(5)]
    top_tools = [tool_display[k] for k, _ in tool_counts.most_common(5)]
    return top_tags, top_tools


def _mean_centroid(members: list[AtlasItem]) -> list[float]:
    vecs = [it.vec for it in members if it.vec]
    if not vecs:
        return []
    dim = len(vecs[0])
    valid = [v for v in vecs if len(v) == dim]
    if not valid:
        return []
    mean = [sum(col) / len(valid) for col in zip(*valid)]
    return _l2_normalize(mean)


def _assign_cluster_colors(clusters: list[AtlasCluster], hints: dict[str, int | None]) -> None:
    """Assign balanced mineral archive colors (muted for uncategorized)."""
    used_slots: Counter[int] = Counter()
    for c in clusters:
        if c.id == "cluster:uncategorized":
            c.color_slot = MUTED_COLOR_SLOT
            c.color = MUTED_COLOR_HEX
            continue
        hint = hints.get(c.id)
        if hint is None and c.category:
            hint = _CATEGORY_SLOT_HINTS.get(normalize_category_key(c.category))
        if hint is not None and 0 <= hint < len(ATLAS_PALETTE):
            slot_idx = hint
        else:
            # Pick least-used slot deterministically
            slot_idx = min(range(len(ATLAS_PALETTE)), key=lambda i: (used_slots[i], i))
        used_slots[slot_idx] += 1
        c.color_slot, c.color = ATLAS_PALETTE[slot_idx]


def build_clusters(items: list[AtlasItem]) -> tuple[list[AtlasCluster], dict[str, str]]:
    """Group materials into balanced clusters, splitting large categories (>25) into subclusters."""
    if not items:
        return [], {}

    # Deterministic order before clustering
    sorted_items = sorted(items, key=lambda it: it.id)
    by_cat: dict[str, list[AtlasItem]] = defaultdict(list)
    cat_display_by_key: dict[str, str] = {}
    for it in sorted_items:
        ck = it.category_key
        by_cat[ck].append(it)
        if ck and it.category and ck not in cat_display_by_key:
            cat_display_by_key[ck] = display_category_name(it.category, ck)

    clusters: list[AtlasCluster] = []
    color_hints: dict[str, int | None] = {}

    # Process non-empty categories largest-first, uncategorized last
    cat_keys = sorted(
        [k for k in by_cat if k],
        key=lambda k: (-len(by_cat[k]), k),
    )
    for ck in cat_keys:
        group = by_cat[ck]
        cat_label = cat_display_by_key.get(ck) or display_category_name(ck, ck)
        if len(group) <= SUBCLUSTER_SPLIT_THRESHOLD:
            scores = [it.score for it in group if it.score is not None]
            top_tags, top_tools = _top_facets_for_members(group, category_name=cat_label)
            cid = f"cluster:{ck}"
            clusters.append(
                AtlasCluster(
                    id=cid,
                    label=cat_label,
                    category=cat_label,
                    color="",
                    color_slot="",
                    member_ids=[it.id for it in group],
                    avg_score=round(sum(scores) / len(scores), 2) if scores else None,
                    top_tags=top_tags,
                    top_tools=top_tools,
                    centroid=_mean_centroid(group),
                )
            )
            color_hints[cid] = _CATEGORY_SLOT_HINTS.get(ck)
        else:
            k = max(SUBCLUSTER_MIN_K, min(SUBCLUSTER_MAX_K, round(len(group) / SUBCLUSTER_TARGET_SIZE)))
            vecs = _build_subcluster_matrix(group)
            raw_labels = _spherical_kmeans(vecs, k, seed=42)
            merged_labels = _merge_tiny_subclusters(group, vecs, raw_labels)

            sub_buckets: dict[int, list[AtlasItem]] = defaultdict(list)
            for it, lbl in zip(group, merged_labels):
                sub_buckets[lbl].append(it)
            ordered_subs = sorted(sub_buckets.values(), key=lambda g: (-len(g), g[0].id))
            named_subs = _pick_subcluster_labels(cat_label, ordered_subs)

            for idx, (sub_members, (sub_label, slot_hint)) in enumerate(zip(ordered_subs, named_subs), start=1):
                scores = [it.score for it in sub_members if it.score is not None]
                top_tags, top_tools = _top_facets_for_members(sub_members, category_name=cat_label)
                cid = f"cluster:{ck}:{idx}"
                clusters.append(
                    AtlasCluster(
                        id=cid,
                        label=sub_label,
                        category=cat_label,
                        color="",
                        color_slot="",
                        member_ids=[it.id for it in sub_members],
                        avg_score=round(sum(scores) / len(scores), 2) if scores else None,
                        top_tags=top_tags,
                        top_tools=top_tools,
                        centroid=_mean_centroid(sub_members),
                    )
                )
                color_hints[cid] = slot_hint

    # Uncategorized cluster ("Неразобранное")
    uncat = by_cat.get("", [])
    if uncat:
        scores = [it.score for it in uncat if it.score is not None]
        top_tags, top_tools = _top_facets_for_members(uncat, category_name=None)
        clusters.append(
            AtlasCluster(
                id="cluster:uncategorized",
                label="Неразобранное",
                category=None,
                color=MUTED_COLOR_HEX,
                color_slot=MUTED_COLOR_SLOT,
                member_ids=[it.id for it in uncat],
                avg_score=round(sum(scores) / len(scores), 2) if scores else None,
                top_tags=top_tags,
                top_tools=top_tools,
                centroid=_mean_centroid(uncat),
            )
        )

    # Sort clusters largest first (uncategorized at end)
    clusters.sort(key=lambda c: (1 if c.id == "cluster:uncategorized" else 0, -len(c.member_ids), c.label))
    _assign_cluster_colors(clusters, color_hints)

    node_to_cluster: dict[str, str] = {}
    for c in clusters:
        for mid in c.member_ids:
            node_to_cluster[mid] = c.id

    return clusters, node_to_cluster


def compute_cluster_edges(
    clusters: list[AtlasCluster],
    node_to_cluster: dict[str, str],
    edges: list[dict[str, Any]],
    *,
    max_bridges: int = 12,
) -> list[dict[str, Any]]:
    """Aggregate strongest inter-cluster bridges from cross-cluster edges + centroid affinity."""
    if len(clusters) < 2:
        return []

    by_id = {c.id: c for c in clusters}
    pair_edge_count: Counter[tuple[str, str]] = Counter()
    pair_tools: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for e in edges:
        ca = node_to_cluster.get(e["source"])
        cb = node_to_cluster.get(e["target"])
        if not ca or not cb or ca == cb:
            continue
        pair = (ca, cb) if ca < cb else (cb, ca)
        pair_edge_count[pair] += 1
        if e.get("reason") == "shared_tool" and e.get("label"):
            for tok in str(e["label"]).split(","):
                tok = tok.strip()
                if tok:
                    pair_tools[pair][tok] += 1

    candidates: list[tuple[float, str, str, int, float, list[str]]] = []
    cids = sorted(by_id.keys())
    for i in range(len(cids)):
        ca = cids[i]
        cent_a = by_id[ca].centroid
        for j in range(i + 1, len(cids)):
            cb = cids[j]
            cent_b = by_id[cb].centroid
            pair = (ca, cb)
            shared_n = pair_edge_count.get(pair, 0)
            sim = _dot(cent_a, cent_b) if (cent_a and cent_b and len(cent_a) == len(cent_b)) else 0.0
            # Keep pairs with real cross-cluster links or high centroid similarity
            if shared_n == 0 and sim < 0.35:
                continue
            strength = shared_n * 1.5 + max(0.0, sim) * 4.0
            top_t = [t for t, _ in pair_tools[pair].most_common(3)]
            candidates.append((strength, ca, cb, shared_n, round(max(0.0, sim), 3), top_t))

    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    out: list[dict[str, Any]] = []
    cluster_deg: Counter[str] = Counter()
    for strength, ca, cb, shared_n, sim, top_t in candidates:
        if len(out) >= max_bridges:
            break
        if cluster_deg[ca] >= 4 or cluster_deg[cb] >= 4:
            continue
        out.append(
            {
                "source": ca,
                "target": cb,
                "weight": max(1, shared_n if shared_n > 0 else int(round(sim * 10))),
                "shared_edges": shared_n,
                "similarity": sim,
                "top_tools": top_t,
            }
        )
        cluster_deg[ca] += 1
        cluster_deg[cb] += 1
    return out


def _cluster_radius(count: int) -> float:
    return round(max(52.0, 28.0 + 18.0 * math.sqrt(max(1, count))), 1)


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain 2D convex hull."""
    pts = sorted(set((round(x, 2), round(y, 2)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def _build_cluster_hull(
    cx: float,
    cy: float,
    radius: float,
    member_coords: list[tuple[float, float]],
) -> list[list[float]]:
    """Padded polygon hull around member coordinates (or smooth octagon for 1-2 points)."""
    if len(member_coords) < 3:
        r = max(36.0, radius * 0.78)
        return [
            [round(cx + r * math.cos(2 * math.pi * i / 8), 1), round(cy + r * math.sin(2 * math.pi * i / 8), 1)]
            for i in range(8)
        ]

    # Add 4 padded corner points around each node so the hull wraps cleanly with breathing room
    pad = 24.0
    expanded: list[tuple[float, float]] = []
    for px, py in member_coords:
        expanded.append((px + pad, py))
        expanded.append((px - pad, py))
        expanded.append((px, py + pad))
        expanded.append((px, py - pad))
        expanded.append((px + pad * 0.72, py + pad * 0.72))
        expanded.append((px - pad * 0.72, py + pad * 0.72))
        expanded.append((px + pad * 0.72, py - pad * 0.72))
        expanded.append((px - pad * 0.72, py - pad * 0.72))

    hull = _convex_hull(expanded)
    return [[round(x, 1), round(y, 1)] for x, y in hull]


def _local_2d_angles(items: list[AtlasItem]) -> list[float]:
    """Deterministic semantic angle per item inside a cluster via 2-step power iteration."""
    n = len(items)
    if n <= 1:
        return [0.0] * n
    vecs = [it.vec for it in items]
    dim = len(vecs[0]) if vecs and vecs[0] else 0
    if dim < 2 or not all(len(v) == dim for v in vecs):
        return [i * 2.399963229728653 for i in range(n)]

    if np is not None:
        X = np.asarray(vecs, dtype=np.float32)
        X = X - X.mean(axis=0, keepdims=True)
        # Deterministic 2D projection via SVD on centered local matrix
        try:
            u, s, _vt = np.linalg.svd(X, full_matrices=False)
            if u.shape[1] >= 2 and float(s[0]) > 1e-6:
                px = u[:, 0] * s[0]
                py = u[:, 1] * s[1]
                return [math.atan2(float(py[i]), float(px[i])) for i in range(n)]
        except Exception:
            pass

    return [i * 2.399963229728653 for i in range(n)]


def layout_atlas(
    clusters: list[AtlasCluster],
    items_by_id: dict[str, AtlasItem],
    cluster_edges: list[dict[str, Any]],
    client_links: dict[str, list[str]],
) -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
    """Compute deterministic 16:9 cluster coordinates, node coordinates, client coords, and hulls."""
    if not clusters:
        return {}, {}

    # 1. Cluster radii & initial 16:9 grid/spiral placement
    for c in clusters:
        c.radius = _cluster_radius(len(c.member_ids))

    n_c = len(clusters)
    golden = 2.399963229728653
    xs = [0.0] * n_c
    ys = [0.0] * n_c
    for i, c in enumerate(clusters):
        if i == 0:
            xs[i], ys[i] = -60.0, 0.0
        elif c.id == "cluster:uncategorized":
            xs[i], ys[i] = 460.0, 210.0
        else:
            ring = 145.0 * math.sqrt(i)
            ang = i * golden
            # Stretch horizontally for 16:9 landscape distribution
            xs[i] = ring * 1.72 * math.cos(ang)
            ys[i] = ring * 0.78 * math.sin(ang)

    # Index inter-cluster attraction
    cid_to_idx = {c.id: i for i, c in enumerate(clusters)}
    attractions: list[tuple[int, int, float]] = []
    for ce in cluster_edges:
        ia = cid_to_idx.get(ce["source"])
        ib = cid_to_idx.get(ce["target"])
        if ia is not None and ib is not None:
            attractions.append((ia, ib, min(2.0, 0.4 + 0.15 * float(ce.get("weight") or 1))))

    # 2. Deterministic weighted relaxation: hard non-overlap + gentle bridge pull + 16:9 gravity
    cluster_gap = 66.0
    for step in range(200):
        # Gentle bridge attraction (stronger early, cools down)
        cool = max(0.15, 1.0 - step / 200.0)
        for ia, ib, w in attractions:
            dx = xs[ib] - xs[ia]
            dy = ys[ib] - ys[ia]
            dist = math.hypot(dx, dy) or 1.0
            ideal = clusters[ia].radius + clusters[ib].radius + cluster_gap + 28.0
            if dist > ideal:
                pull = (dist - ideal) * 0.015 * w * cool
                ux, uy = dx / dist, dy / dist
                xs[ia] += ux * pull
                ys[ia] += uy * pull
                xs[ib] -= ux * pull
                ys[ib] -= uy * pull

        # Anisotropic 16:9 gravity (stronger Y pull keeps map wide rather than tall)
        for i, c in enumerate(clusters):
            if c.id == "cluster:uncategorized":
                # Gently keep uncategorized calm on the outer lower-right flank
                xs[i] += (420.0 - xs[i]) * 0.008 * cool
                ys[i] += (160.0 - ys[i]) * 0.014 * cool
            else:
                xs[i] *= 1.0 - 0.006 * cool
                ys[i] *= 1.0 - 0.036 * cool

        # Hard non-overlap separation between all cluster territories
        for i in range(n_c):
            for j in range(i + 1, n_c):
                dx = xs[j] - xs[i]
                dy = ys[j] - ys[i]
                dist = math.hypot(dx, dy)
                min_d = clusters[i].radius + clusters[j].radius + cluster_gap
                if dist < min_d:
                    if dist < 1e-6:
                        ang = (i * 1.7 + j * 2.9)
                        ux, uy = math.cos(ang), math.sin(ang)
                    else:
                        ux, uy = dx / dist, dy / dist
                    overlap = (min_d - dist) * 0.54
                    xs[i] -= ux * overlap
                    ys[i] -= uy * overlap
                    xs[j] += ux * overlap
                    ys[j] += uy * overlap

    # Center the entire archipelago around (0, 0)
    min_x = min(xs[i] - clusters[i].radius for i in range(n_c))
    max_x = max(xs[i] + clusters[i].radius for i in range(n_c))
    min_y = min(ys[i] - clusters[i].radius for i in range(n_c))
    max_y = max(ys[i] + clusters[i].radius for i in range(n_c))
    cx_shift = (min_x + max_x) * 0.5
    cy_shift = (min_y + max_y) * 0.5
    for i, c in enumerate(clusters):
        c.x = round(xs[i] - cx_shift, 1)
        c.y = round(ys[i] - cy_shift, 1)

    # 3. Deterministic intra-cluster placement (high score closer to core, semantic angle, zero overlap)
    node_coords: dict[str, tuple[float, float]] = {}
    for c in clusters:
        members = [items_by_id[mid] for mid in c.member_ids if mid in items_by_id]
        # Sort by usefulness score desc (core = best materials), then id for determinism
        members.sort(key=lambda it: (-(it.score or 0), it.id))
        m_n = len(members)
        if m_n == 0:
            c.hull = _build_cluster_hull(c.x, c.y, c.radius, [])
            continue
        if m_n == 1:
            node_coords[members[0].id] = (c.x, c.y)
            c.hull = _build_cluster_hull(c.x, c.y, c.radius, [(c.x, c.y)])
            continue

        sem_angles = _local_2d_angles(members)
        max_r = max(24.0, c.radius * 0.80)
        pts_x: list[float] = []
        pts_y: list[float] = []
        for idx, (_it, sem_ang) in enumerate(zip(members, sem_angles)):
            # Blend semantic direction with golden-angle spiral to avoid angular bunching
            frac = math.sqrt((idx + 0.5) / m_n)
            r = max_r * frac * 0.92
            ang = sem_ang * 0.55 + (idx * golden) * 0.45
            pts_x.append(c.x + r * math.cos(ang))
            pts_y.append(c.y + r * math.sin(ang))

        # Local collision relaxation so points never clump or overlap
        min_node_dist = 20.0
        for _ in range(40):
            for i in range(m_n):
                for j in range(i + 1, m_n):
                    dx = pts_x[j] - pts_x[i]
                    dy = pts_y[j] - pts_y[i]
                    dist = math.hypot(dx, dy)
                    if dist < min_node_dist:
                        if dist < 1e-6:
                            ang = (i * 2.1 + j * 1.3)
                            ux, uy = math.cos(ang), math.sin(ang)
                        else:
                            ux, uy = dx / dist, dy / dist
                        push = (min_node_dist - dist) * 0.52
                        pts_x[i] -= ux * push
                        pts_y[i] -= uy * push
                        pts_x[j] += ux * push
                        pts_y[j] += uy * push
            # Soft containment inside cluster disc
            for i in range(m_n):
                dx = pts_x[i] - c.x
                dy = pts_y[i] - c.y
                d = math.hypot(dx, dy)
                if d > max_r:
                    scale = max_r / d
                    pts_x[i] = c.x + dx * scale
                    pts_y[i] = c.y + dy * scale

        member_xy: list[tuple[float, float]] = []
        for it, px, py in zip(members, pts_x, pts_y):
            rx, ry = round(px, 1), round(py, 1)
            node_coords[it.id] = (rx, ry)
            member_xy.append((rx, ry))

        c.hull = _build_cluster_hull(c.x, c.y, c.radius, member_xy)

    # 4. Place client nodes cleanly near centroid of their linked materials
    client_coords: dict[str, tuple[float, float]] = {}
    all_placed = list(node_coords.values())
    for cid in sorted(client_links.keys()):
        mids = [m for m in client_links[cid] if m in node_coords]
        if mids:
            cx = sum(node_coords[m][0] for m in mids) / len(mids)
            cy = sum(node_coords[m][1] for m in mids) / len(mids)
            norm = math.hypot(cx, cy) or 1.0
            px = cx + (cx / norm) * 32.0
            py = cy + (cy / norm) * 32.0
        else:
            px, py = 0.0, 0.0

        # Nudge away from any existing point
        for _ in range(15):
            for ox, oy in all_placed:
                dx = px - ox
                dy = py - oy
                d = math.hypot(dx, dy)
                if d < 24.0:
                    if d < 1e-6:
                        ux, uy = 1.0, 0.0
                    else:
                        ux, uy = dx / d, dy / d
                    px += ux * (24.0 - d)
                    py += uy * (24.0 - d)

        rx, ry = round(px, 1), round(py, 1)
        client_coords[cid] = (rx, ry)
        all_placed.append((rx, ry))

    return node_coords, client_coords
