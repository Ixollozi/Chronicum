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

# Threshold above which a single category (or uncategorized pool) auto-splits into thematic subclusters
SUBCLUSTER_SPLIT_THRESHOLD = 25
SUBCLUSTER_TARGET_SIZE = 18
SUBCLUSTER_MIN_K = 2
SUBCLUSTER_MAX_K = 18
SUBCLUSTER_MIN_MEMBER_COUNT = 5

# Chronicum Dark Archive mineral palette (16 distinct muted tones; no neon / purple glow)
ATLAS_PALETTE: list[tuple[str, str]] = [
    ("0", "#c4a574"),  # brass
    ("1", "#7ba892"),  # sage
    ("2", "#7496b8"),  # slate blue
    ("3", "#c9886b"),  # terracotta
    ("4", "#a3ad70"),  # olive
    ("5", "#6ea3a8"),  # sea slate
    ("6", "#b88292"),  # dusty rose
    ("7", "#c29258"),  # warm ochre
    ("8", "#5f9d8c"),  # verdigris
    ("9", "#8ba2c7"),  # cerulean slate
    ("10", "#c47670"),  # muted rust
    ("11", "#86a870"),  # moss green
    ("12", "#5d92a8"),  # fjord blue
    ("13", "#b8967c"),  # sandstone
    ("14", "#a9a362"),  # antique lichen
    ("15", "#9d8898"),  # muted mauve-slate
]
MUTED_COLOR_SLOT = "muted"
MUTED_COLOR_HEX = "#6c7570"
_MUTED_SUBCLUSTER_HEXES: tuple[str, ...] = (
    "#6c7570",
    "#7e8781",
    "#5f6c6a",
    "#86887d",
    "#6d7682",
    "#7f7872",
)

# Preferred color slot by category keyword (distinct per category for visual stability)
_CATEGORY_SLOT_HINTS: dict[str, int] = {
    "ai": 1,
    "программирование": 2,
    "маркетинг": 3,
    "дизайн": 5,
    "бизнес": 7,
    "продажи": 10,
    "фриланс": 6,
    "игры": 12,
}

# Broad umbrella categories where cross-domain archetypes clarify subclusters;
# narrow domain categories (Дизайн, Бизнес, Маркетинг, Продажи, ...) use contrastive tag/tool/title naming instead.
_UMBRELLA_ARCHETYPE_CATEGORIES: frozenset[str] = frozenset(
    {"", "ai", "неразобранное", "разное", "прочее", "общее", "other", "misc"}
)

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
        "Контент и SEO",
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
    color_hint: int | None = None


def _is_uncategorized_cluster(cid: str) -> bool:
    return cid == "cluster:uncategorized" or cid.startswith("cluster:uncategorized:")


def _target_subcluster_k(n: int) -> int:
    """Deterministic subcluster count without banker's rounding or a low hard cap."""
    if n <= SUBCLUSTER_SPLIT_THRESHOLD:
        return 1
    if n <= 108:
        return max(SUBCLUSTER_MIN_K, math.ceil(n / SUBCLUSTER_TARGET_SIZE))
    return min(SUBCLUSTER_MAX_K, max(6, int(math.ceil(math.sqrt(n * 0.32)))))


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

    # Add light archetype signal tokens only for broad umbrella categories (AI / uncategorized)
    if item.category_key in _UMBRELLA_ARCHETYPE_CATEGORIES:
        blob = f"{item.title} {' '.join(clean_tags)} {' '.join(item.tools)} {(item.summary or '')[:200]}".lower().replace("ё", "е")
        for arch_key, _label, _slot, stems in _THEMATIC_ARCHETYPES:
            hits = sum(1 for st in stems if st in blob)
            if hits:
                counts[f"arch:{arch_key}"] += min(4, hits * 2)
    return counts


def _build_subcluster_matrix(items: list[AtlasItem]) -> Any:
    """Combine L2-normalized embedding vectors + TF-IDF thematic features per item."""
    n = len(items)
    token_bags = [_item_feature_tokens(it) for it in items]
    df: Counter[str] = Counter()
    for bag in token_bags:
        for tok in bag:
            df[tok] += 1

    # Keep informative vocabulary (df >= 2 and not in > 85% of items); cap wide vocabs deterministically
    max_df = max(2, int(n * 0.85))
    vocab_cands = [tok for tok, cnt in df.items() if 2 <= cnt <= max_df]
    if len(vocab_cands) > 160:
        vocab_cands.sort(key=lambda t: (-df[t], t))
        vocab = sorted(vocab_cands[:160])
    else:
        vocab = sorted(vocab_cands)
    tok_to_idx = {tok: i for i, tok in enumerate(vocab)}
    idf_arr = [math.log((n + 1.0) / (df[tok] + 1.0)) + 1.0 for tok in vocab]

    emb_dim = max((len(it.vec) for it in items), default=0)
    v_dim = len(vocab)

    if np is not None:
        X = np.zeros((n, emb_dim + v_dim), dtype=np.float32)
        if emb_dim > 0:
            for i, it in enumerate(items):
                if len(it.vec) == emb_dim:
                    X[i, :emb_dim] = it.vec
            e_norms = np.linalg.norm(X[:, :emb_dim], axis=1, keepdims=True)
            e_norms = np.where(e_norms > 1e-12, e_norms, 1.0)
            X[:, :emb_dim] /= e_norms
        if v_dim > 0:
            for i, bag in enumerate(token_bags):
                for tok, cnt in bag.items():
                    idx = tok_to_idx.get(tok)
                    if idx is not None:
                        X[i, emb_dim + idx] = (1.0 + math.log(cnt)) * idf_arr[idx]
            t_norms = np.linalg.norm(X[:, emb_dim:], axis=1, keepdims=True)
            t_norms = np.where(t_norms > 1e-12, t_norms, 1.0)
            X[:, emb_dim:] = (X[:, emb_dim:] / t_norms) * 1.45
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        norms = np.where(norms > 1e-12, norms, 1.0)
        X /= norms
        return X

    combined: list[list[float]] = []
    for it, bag in zip(items, token_bags):
        emb_part = _l2_normalize(it.vec) if len(it.vec) == emb_dim and emb_dim > 0 else [0.0] * emb_dim
        tf_part = [0.0] * v_dim
        for tok, cnt in bag.items():
            idx = tok_to_idx.get(tok)
            if idx is not None:
                tf_part[idx] = (1.0 + math.log(cnt)) * idf_arr[idx]
        tf_part = _l2_normalize(tf_part) if v_dim else []
        # Blend: embeddings carry broad semantics; TF-IDF tags/tools/archetypes sharpen boundaries
        vec = [v * 1.0 for v in emb_part] + [v * 1.45 for v in tf_part]
        combined.append(_l2_normalize(vec))
    return combined


def _spherical_kmeans(
    vectors: Any,
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

    dim = len(vectors[0]) if n > 0 else 0
    if dim == 0:
        return [i % k for i in range(n)]

    eff_restarts = 2 if n > 120 else restarts

    if np is not None:
        X = vectors if isinstance(vectors, np.ndarray) else np.asarray(vectors, dtype=np.float32)
        best_labels: list[int] | None = None
        best_obj = -1e18

        for r in range(eff_restarts):
            rng = random.Random(seed + r * 997)
            # k-means++ deterministic init using cosine distance (1 - cos)
            first = rng.randrange(n)
            centroid_indices = [first]
            chosen_set = {first}
            min_dists = np.maximum(0.0, 1.0 - (X @ X[first]))
            for _ in range(1, k):
                dists_sq = min_dists * min_dists
                total = float(dists_sq.sum())
                if total <= 1e-12:
                    nxt = next((idx for idx in range(n) if idx not in chosen_set), 0)
                else:
                    pick = rng.random() * total
                    cdf = np.cumsum(dists_sq)
                    nxt = int(np.searchsorted(cdf, pick, side="left"))
                    if nxt >= n:
                        nxt = n - 1
                centroid_indices.append(nxt)
                chosen_set.add(nxt)
                d_new = np.maximum(0.0, 1.0 - (X @ X[nxt]))
                min_dists = np.minimum(min_dists, d_new)

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
    vectors: Any,
    labels: list[int],
    min_size: int = SUBCLUSTER_MIN_MEMBER_COUNT,
) -> list[int]:
    """Fold degenerate tiny subclusters (< min_size) into their nearest healthy sibling."""
    is_np = np is not None and isinstance(vectors, np.ndarray)
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

        target_tiny = min(tiny, key=lambda l: (len(groups[l]), l))
        if is_np:
            h_cents = []
            for h in healthy:
                mean = vectors[groups[h]].mean(axis=0)
                norm = float(np.linalg.norm(mean))
                h_cents.append(mean / norm if norm > 1e-12 else mean)
            H = np.stack(h_cents, axis=0)
            tiny_idxs = groups[target_tiny]
            sims = vectors[tiny_idxs] @ H.T
            best_pos = sims.argmax(axis=1).tolist()
            for idx, pos in zip(tiny_idxs, best_pos):
                labels[idx] = healthy[pos]
        else:
            centroids: dict[int, list[float]] = {}
            for lbl in healthy:
                idxs = groups[lbl]
                mean = [sum(col) / len(idxs) for col in zip(*(vectors[i] for i in idxs))]
                centroids[lbl] = _l2_normalize(mean)
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
    """Assign distinct, readable Russian subcluster names + optional palette slot hint.

    Archetype names apply only to broad umbrella categories (AI, Неразобранное, ...);
    narrow domain categories use contrastive tags/tools/title terms without parent tautology.
    """
    all_items = [it for g in groups for it in g]
    parent_key = normalize_category_key(parent_label)
    parent_words = set(_tokenize_text(parent_label))
    if parent_key:
        parent_words.add(parent_key)
    use_archetypes = parent_key in _UMBRELLA_ARCHETYPE_CATEGORIES

    # Global doc frequency of tools, clean tags, and title keywords inside parent for contrastive lift
    global_tag_df: Counter[str] = Counter()
    global_tool_df: Counter[str] = Counter()
    global_kw_df: Counter[str] = Counter()
    for it in all_items:
        for t in set(clean_graph_tags(it.tags, category_name=it.category, include_meta=False)):
            global_tag_df[normalize_key(t)] += 1
        for tool in set(normalize_key(x) for x in it.tools if x):
            if tool and tool not in _GENERIC_CLUSTER_TOKENS:
                global_tool_df[tool] += 1
        for kw in set(_tokenize_text(it.title)):
            if kw not in parent_words:
                global_kw_df[kw] += 1

    assigned_arch: dict[int, tuple[str, int | None]] = {}
    if use_archetypes:
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
            for arch_key, arch_label, _slot, stems in _THEMATIC_ARCHETYPES:
                # Never allow an archetype whose wording duplicates the parent label
                arch_words = set(_tokenize_text(arch_label))
                if parent_words and (parent_words & arch_words):
                    g_scores[arch_key] = 0.0
                    continue
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
                g_scores[arch_key] = (
                    (rate * 0.65 + (weighted_hits / max(1, len(g))) * 0.2) * (0.5 + 0.5 * lift)
                    if rate >= 0.20
                    else 0.0
                )
            arch_scores.append(g_scores)

        used_arch: set[str] = set()
        # Only attach palette hints on the primary AI umbrella so other pools never collide on one slot
        arch_meta = {
            k: (lbl, slot if parent_key == "ai" else None)
            for k, lbl, slot, _ in _THEMATIC_ARCHETYPES
        }

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

    def _clean_term_for_parent(disp: str) -> str:
        """Strip redundant parent prefix from multi-word terms (e.g. 'AI монтаж' under 'AI' -> 'Монтаж')."""
        cleaned = disp.strip()
        if not cleaned or not parent_key:
            return cleaned
        parts = cleaned.split()
        if len(parts) >= 2:
            head_key = normalize_key(parts[0])
            if head_key == parent_key or head_key in parent_words:
                tail = " ".join(parts[1:]).strip()
                if tail and len(tail) >= 2:
                    return tail[0].upper() + tail[1:] if tail[0].islower() else tail
        return cleaned

    def _is_redundant_with_parent(term_key: str) -> bool:
        if not term_key:
            return True
        if term_key == parent_key:
            return True
        if parent_key and len(parent_key) >= 4 and (term_key in parent_key or parent_key in term_key):
            return True
        term_words = set(_tokenize_text(term_key))
        return bool(term_words and parent_words and term_words.issubset(parent_words))

    # Build final unique labels; fall back to contrastive top tag/tool/title keyword
    results: list[tuple[str, int | None]] = []
    used_labels: set[str] = set()
    used_suffixes: set[str] = set()
    for gi, g in enumerate(groups):
        if gi in assigned_arch:
            suffix, slot_hint = assigned_arch[gi]
            label = f"{parent_label} · {suffix}"
            used_labels.add(label)
            used_suffixes.add(suffix.casefold())
            results.append((label, slot_hint))
            continue

        local_terms: Counter[str] = Counter()
        display_map: dict[str, str] = {}
        for it in g:
            for t in clean_graph_tags(it.tags, category_name=it.category, include_meta=False):
                cleaned_disp = _clean_term_for_parent(display_tag_name(t, normalize_key(t)))
                k = normalize_key(cleaned_disp)
                if k and k not in _GENERIC_CLUSTER_TOKENS and not _is_redundant_with_parent(k):
                    local_terms[k] += 2
                    display_map.setdefault(k, cleaned_disp)
            for tool in it.tools:
                cleaned_disp = _clean_term_for_parent(tool.strip())
                k = normalize_key(cleaned_disp)
                if k and len(k) >= 2 and k not in _GENERIC_CLUSTER_TOKENS and not _is_redundant_with_parent(k):
                    local_terms[k] += 1
                    display_map.setdefault(k, cleaned_disp)

        ranked = sorted(
            local_terms.items(),
            key=lambda kv: (
                -(kv[1] / max(1, len(g)))
                * math.log((len(all_items) + 1.0) / (global_tag_df[kv[0]] + global_tool_df[kv[0]] + 1.0)),
                -kv[1],
                kv[0],
            ),
        )

        # Pick 1-2 non-overlapping display terms
        picks: list[str] = []
        picked_keys: set[str] = set()
        for k, _ in ranked:
            if len(picks) >= 2:
                break
            disp = display_map[k]
            if any(k in pk or pk in k for pk in picked_keys):
                continue
            picks.append(disp)
            picked_keys.add(k)

        # Fallback to contrastive title keywords when tags/tools are absent or sparse
        if not picks:
            local_kw: Counter[str] = Counter()
            kw_display: dict[str, str] = {}
            for it in g:
                for raw_tok in _TOKEN_RE.findall(it.title or ""):
                    k = raw_tok.replace("ё", "е").replace("Ё", "Е").lower()
                    if len(k) < 4 or k in _GENERIC_CLUSTER_TOKENS or _is_redundant_with_parent(k):
                        continue
                    local_kw[k] += 1
                    kw_display.setdefault(k, raw_tok.capitalize() if raw_tok.islower() else raw_tok)
            ranked_kw = sorted(
                local_kw.items(),
                key=lambda kv: (
                    -(kv[1] / max(1, len(g)))
                    * math.log((len(all_items) + 1.0) / (global_kw_df[kv[0]] + 1.0)),
                    -kv[1],
                    kv[0],
                ),
            )
            picks = [kw_display[k] for k, _ in ranked_kw[:2]]

        if picks:
            two_part = " и ".join(picks)
            # Keep Macro pills crisp (<= 26 chars): prefer single sharp term when two terms overflow
            if len(picks) >= 2 and len(f"{parent_label} · {two_part}") > 26 and picks[0].casefold() not in used_suffixes:
                suffix = picks[0]
            else:
                suffix = two_part
            # If the exact suffix was already taken, try distinct single or paired candidate
            if suffix.casefold() in used_suffixes:
                resolved = False
                if len(picks) >= 2 and two_part.casefold() not in used_suffixes:
                    suffix = two_part
                    resolved = True
                if not resolved and len(ranked) > 1:
                    for alt_k, _ in ranked[1:8]:
                        alt_single = display_map[alt_k]
                        if len(f"{parent_label} · {alt_single}") <= 26 and alt_single.casefold() not in used_suffixes:
                            suffix = alt_single
                            resolved = True
                            break
                        alt_pair = f"{picks[0]} и {alt_single}"
                        if alt_pair.casefold() not in used_suffixes:
                            suffix = alt_pair
                            resolved = True
                            break
        else:
            suffix = f"Группа {gi + 1}"

        label = f"{parent_label} · {suffix}"
        if label in used_labels:
            label = f"{parent_label} · {suffix} #{gi + 1}"
        used_labels.add(label)
        used_suffixes.add(suffix.casefold())
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
    """Assign initial balanced mineral archive colors (muted for uncategorized), avoiding duplicate hints."""
    n_palette = len(ATLAS_PALETTE)
    used_slots: Counter[int] = Counter()
    uncat_idx = 0
    for c in clusters:
        if _is_uncategorized_cluster(c.id):
            c.color_slot = MUTED_COLOR_SLOT
            c.color = (
                MUTED_COLOR_HEX
                if c.id == "cluster:uncategorized"
                else _MUTED_SUBCLUSTER_HEXES[uncat_idx % len(_MUTED_SUBCLUSTER_HEXES)]
            )
            uncat_idx += 1
            continue

        hint = hints.get(c.id)
        if hint is None:
            hint = c.color_hint
        c.color_hint = hint
        # Honor hint only if it hasn't been used yet; otherwise pick globally least-used slot
        if hint is not None and 0 <= hint < n_palette and used_slots[hint] == 0:
            slot_idx = hint
        else:
            slot_idx = min(range(n_palette), key=lambda i: (used_slots[i], i))
        used_slots[slot_idx] += 1
        c.color_slot, c.color = ATLAS_PALETTE[slot_idx]


def _assign_spatial_cluster_colors(
    clusters: list[AtlasCluster],
    cluster_edges: list[dict[str, Any]],
) -> None:
    """Graph-coloring over spatially adjacent & bridge-linked clusters after layout.

    Guarantees that no two neighboring clusters share the same color, while respecting
    soft category hints whenever conflict-free and balancing palette usage globally.
    """
    n_c = len(clusters)
    if n_c == 0:
        return

    n_palette = len(ATLAS_PALETTE)
    cid_to_idx = {c.id: i for i, c in enumerate(clusters)}

    # Build adjacency graph: spatially close clusters + explicit cluster bridges
    adj: list[set[int]] = [set() for _ in range(n_c)]
    proximity_pad = 150.0
    for i in range(n_c):
        ci = clusters[i]
        for j in range(i + 1, n_c):
            cj = clusters[j]
            dist = math.hypot(ci.x - cj.x, ci.y - cj.y)
            if dist < ci.radius + cj.radius + proximity_pad:
                adj[i].add(j)
                adj[j].add(i)
    for ce in cluster_edges:
        ia = cid_to_idx.get(ce["source"])
        ib = cid_to_idx.get(ce["target"])
        if ia is not None and ib is not None and ia != ib:
            adj[ia].add(ib)
            adj[ib].add(ia)

    # 1. Color uncategorized clusters with neighbor-distinct muted archive shades
    uncat_indices = [i for i, c in enumerate(clusters) if _is_uncategorized_cluster(c.id)]
    if len(uncat_indices) == 1:
        u = clusters[uncat_indices[0]]
        u.color_slot = MUTED_COLOR_SLOT
        u.color = MUTED_COLOR_HEX
    elif len(uncat_indices) > 1:
        uncat_assigned: dict[int, int] = {}
        uncat_used: Counter[int] = Counter()
        for idx in sorted(uncat_indices, key=lambda i: (-len(adj[i]), -len(clusters[i].member_ids), clusters[i].id)):
            forbidden_u = {uncat_assigned[nb] for nb in adj[idx] if nb in uncat_assigned}
            avail_u = [s for s in range(len(_MUTED_SUBCLUSTER_HEXES)) if s not in forbidden_u]
            pick_u = min(avail_u or range(len(_MUTED_SUBCLUSTER_HEXES)), key=lambda s: (uncat_used[s], s))
            uncat_assigned[idx] = pick_u
            uncat_used[pick_u] += 1
            clusters[idx].color_slot = MUTED_COLOR_SLOT
            clusters[idx].color = _MUTED_SUBCLUSTER_HEXES[pick_u]

    # 2. DSATUR-style greedy coloring for all regular clusters
    reg_indices = [i for i, c in enumerate(clusters) if not _is_uncategorized_cluster(c.id)]
    if not reg_indices:
        return

    assigned: dict[int, int] = {}
    used_slots: Counter[int] = Counter()
    remaining = set(reg_indices)

    while remaining:
        # Pick uncolored cluster with most distinct neighbor colors (DSATUR), then highest degree, then largest size
        idx = max(
            remaining,
            key=lambda i: (
                len({assigned[nb] for nb in adj[i] if nb in assigned}),
                len(adj[i]),
                len(clusters[i].member_ids),
                -i,
            ),
        )
        remaining.remove(idx)

        c = clusters[idx]
        forbidden = {assigned[nb] for nb in adj[idx] if nb in assigned}
        available = [s for s in range(n_palette) if s not in forbidden]
        if not available:
            # Extreme fallback if a cluster somehow has >= n_palette distinct-color neighbors:
            # pick color colliding with the farthest neighbor(s)
            def _collision_cost(s: int) -> tuple[int, float, int, int]:
                colliding_nbs = [nb for nb in adj[idx] if assigned.get(nb) == s]
                min_d = min(
                    (math.hypot(c.x - clusters[nb].x, c.y - clusters[nb].y) for nb in colliding_nbs),
                    default=1e9,
                )
                return (len(colliding_nbs), -min_d, used_slots[s], s)

            slot_idx = min(range(n_palette), key=_collision_cost)
        else:
            min_used = min(used_slots[s] for s in available)
            hint = c.color_hint
            # Prefer hint when it doesn't collide with neighbors and doesn't unbalance palette usage
            if (
                hint is not None
                and hint in available
                and used_slots[hint] <= min_used
            ):
                slot_idx = hint
            else:
                slot_idx = min(available, key=lambda s: (used_slots[s], s))

        assigned[idx] = slot_idx
        used_slots[slot_idx] += 1
        c.color_slot, c.color = ATLAS_PALETTE[slot_idx]


def build_clusters(items: list[AtlasItem]) -> tuple[list[AtlasCluster], dict[str, str]]:
    """Group materials into balanced clusters, splitting large categories and uncategorized (>25) into subclusters."""
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
            hint = _CATEGORY_SLOT_HINTS.get(ck)
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
                    color_hint=hint,
                )
            )
            color_hints[cid] = hint
        else:
            k = _target_subcluster_k(len(group))
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
                # Give category hint at most to the largest subcluster when no explicit hint is set
                eff_hint = slot_hint if slot_hint is not None else (_CATEGORY_SLOT_HINTS.get(ck) if idx == 1 else None)
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
                        color_hint=eff_hint,
                    )
                )
                color_hints[cid] = eff_hint

    # Uncategorized cluster(s) ("Неразобранное") — auto-subcluster when > SUBCLUSTER_SPLIT_THRESHOLD
    uncat = by_cat.get("", [])
    if uncat:
        if len(uncat) <= SUBCLUSTER_SPLIT_THRESHOLD:
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
        else:
            k = _target_subcluster_k(len(uncat))
            vecs = _build_subcluster_matrix(uncat)
            raw_labels = _spherical_kmeans(vecs, k, seed=42)
            merged_labels = _merge_tiny_subclusters(uncat, vecs, raw_labels)

            sub_buckets_u: dict[int, list[AtlasItem]] = defaultdict(list)
            for it, lbl in zip(uncat, merged_labels):
                sub_buckets_u[lbl].append(it)
            ordered_subs_u = sorted(sub_buckets_u.values(), key=lambda g: (-len(g), g[0].id))
            named_subs_u = _pick_subcluster_labels("Неразобранное", ordered_subs_u)

            for idx, (sub_members, (sub_label, _slot_hint)) in enumerate(zip(ordered_subs_u, named_subs_u), start=1):
                scores = [it.score for it in sub_members if it.score is not None]
                top_tags, top_tools = _top_facets_for_members(sub_members, category_name=None)
                cid = f"cluster:uncategorized:{idx}"
                clusters.append(
                    AtlasCluster(
                        id=cid,
                        label=sub_label,
                        category=None,
                        color=_MUTED_SUBCLUSTER_HEXES[(idx - 1) % len(_MUTED_SUBCLUSTER_HEXES)],
                        color_slot=MUTED_COLOR_SLOT,
                        member_ids=[it.id for it in sub_members],
                        avg_score=round(sum(scores) / len(scores), 2) if scores else None,
                        top_tags=top_tags,
                        top_tools=top_tools,
                        centroid=_mean_centroid(sub_members),
                    )
                )

    # Sort clusters largest first (uncategorized at end)
    clusters.sort(key=lambda c: (1 if _is_uncategorized_cluster(c.id) else 0, -len(c.member_ids), c.label))
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
    max_bridges: int | None = None,
) -> list[dict[str, Any]]:
    """Aggregate strongest inter-cluster bridges from cross-cluster edges + centroid affinity."""
    if len(clusters) < 2:
        return []

    bridge_limit = (
        max_bridges
        if max_bridges is not None
        else max(12, min(48, int(round(len(clusters) * 1.15))))
    )

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
    seen_pairs: set[tuple[str, str]] = set()
    cluster_deg: Counter[str] = Counter()

    def _add_bridge(ca: str, cb: str, shared_n: int, sim: float, top_t: list[str]) -> None:
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
        seen_pairs.add((ca, cb))
        cluster_deg[ca] += 1
        cluster_deg[cb] += 1

    # Pass 1: give disconnected clusters at least one bridge so 30+ cluster maps stay coherent
    for _strength, ca, cb, shared_n, sim, top_t in candidates:
        if len(out) >= bridge_limit:
            break
        if cluster_deg[ca] > 0 and cluster_deg[cb] > 0:
            continue
        if cluster_deg[ca] >= 4 or cluster_deg[cb] >= 4:
            continue
        _add_bridge(ca, cb, shared_n, sim, top_t)

    # Pass 2: fill remaining bridge budget by strength
    for _strength, ca, cb, shared_n, sim, top_t in candidates:
        if len(out) >= bridge_limit:
            break
        if (ca, cb) in seen_pairs:
            continue
        if cluster_deg[ca] >= 4 or cluster_deg[cb] >= 4:
            continue
        _add_bridge(ca, cb, shared_n, sim, top_t)

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
    """Deterministic semantic angle per item inside a cluster via fast m×m Gram eigen-projection."""
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
        try:
            # m×m Gram matrix eigh is O(m^3) vs O(m^2*dim) full SVD (much faster when m << dim)
            G = X @ X.T
            eigvals, eigvecs = np.linalg.eigh(G)
            if eigvals.size >= 2 and float(eigvals[-1]) > 1e-6:
                s0 = math.sqrt(max(0.0, float(eigvals[-1])))
                s1 = math.sqrt(max(0.0, float(eigvals[-2])))
                px = (eigvecs[:, -1] * s0).tolist()
                py = (eigvecs[:, -2] * s1).tolist()
                return [math.atan2(py[i], px[i]) for i in range(n)]
        except Exception:
            pass

    return [i * 2.399963229728653 for i in range(n)]


def _relax_cluster_points_grid(
    cx: float,
    cy: float,
    max_r: float,
    pts_x: list[float],
    pts_y: list[float],
    *,
    min_dist: float = 20.0,
    iterations: int = 24,
) -> None:
    """O(M) spatial-hash-grid collision relaxation inside a cluster disc."""
    m_n = len(pts_x)
    if m_n <= 1:
        return

    inv_cell = 1.0 / min_dist
    min_dist_sq = min_dist * min_dist

    for _ in range(iterations):
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i in range(m_n):
            gx = math.floor(pts_x[i] * inv_cell)
            gy = math.floor(pts_y[i] * inv_cell)
            grid[(gx, gy)].append(i)

        moved = False
        for (gx, gy), cell_pts in grid.items():
            # Check same cell + 4 forward neighbor cells to process each pair once
            neighbor_cells = (
                cell_pts,
                grid.get((gx + 1, gy)),
                grid.get((gx - 1, gy + 1)),
                grid.get((gx, gy + 1)),
                grid.get((gx + 1, gy + 1)),
            )
            for c_idx, other_pts in enumerate(neighbor_cells):
                if not other_pts:
                    continue
                same_cell = c_idx == 0
                for a_pos, i in enumerate(cell_pts):
                    start_b = a_pos + 1 if same_cell else 0
                    for b_pos in range(start_b, len(other_pts)):
                        j = other_pts[b_pos]
                        dx = pts_x[j] - pts_x[i]
                        dy = pts_y[j] - pts_y[i]
                        dist_sq = dx * dx + dy * dy
                        if dist_sq < min_dist_sq:
                            if dist_sq < 1e-12:
                                ang = i * 2.1 + j * 1.3
                                ux, uy = math.cos(ang), math.sin(ang)
                                dist = 0.0
                            else:
                                dist = math.sqrt(dist_sq)
                                ux, uy = dx / dist, dy / dist
                            push = (min_dist - dist) * 0.52
                            pts_x[i] -= ux * push
                            pts_y[i] -= uy * push
                            pts_x[j] += ux * push
                            pts_y[j] += uy * push
                            moved = True

        # Soft containment inside cluster disc
        for i in range(m_n):
            dx = pts_x[i] - cx
            dy = pts_y[i] - cy
            d_sq = dx * dx + dy * dy
            if d_sq > max_r * max_r:
                scale = max_r / math.sqrt(d_sq)
                pts_x[i] = cx + dx * scale
                pts_y[i] = cy + dy * scale

        if not moved:
            break


def layout_atlas(
    clusters: list[AtlasCluster],
    items_by_id: dict[str, AtlasItem],
    cluster_edges: list[dict[str, Any]],
    client_links: dict[str, list[str]],
) -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
    """Compute deterministic 16:9 cluster coordinates, node coordinates, client coords, hulls, and spatial colors."""
    golden = 2.399963229728653
    if not clusters:
        client_coords_only: dict[str, tuple[float, float]] = {}
        for idx, cid in enumerate(sorted(client_links.keys())):
            ang = idx * golden
            r = 45.0 + 18.0 * math.sqrt(idx)
            client_coords_only[cid] = (round(r * math.cos(ang), 1), round(r * math.sin(ang), 1))
        return {}, client_coords_only

    # 1. Cluster radii & initial 16:9 grid/spiral placement
    for c in clusters:
        c.radius = _cluster_radius(len(c.member_ids))

    n_c = len(clusters)
    xs = [0.0] * n_c
    ys = [0.0] * n_c
    uncat_rank = 0
    for i, c in enumerate(clusters):
        if i == 0:
            xs[i], ys[i] = -60.0, 0.0
        elif _is_uncategorized_cluster(c.id):
            xs[i] = 460.0 + (uncat_rank % 3) * 140.0
            ys[i] = 210.0 + (uncat_rank // 3) * 130.0
            uncat_rank += 1
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
    radii = [c.radius for c in clusters]
    is_uncat_flags = [_is_uncategorized_cluster(c.id) for c in clusters]
    max_steps = 110
    for step in range(max_steps):
        # Gentle bridge attraction (stronger early, cools down)
        cool = max(0.15, 1.0 - step / float(max_steps))
        for ia, ib, w in attractions:
            dx = xs[ib] - xs[ia]
            dy = ys[ib] - ys[ia]
            dist = math.hypot(dx, dy) or 1.0
            ideal = radii[ia] + radii[ib] + cluster_gap + 28.0
            if dist > ideal:
                pull = (dist - ideal) * 0.022 * w * cool
                ux, uy = dx / dist, dy / dist
                xs[ia] += ux * pull
                ys[ia] += uy * pull
                xs[ib] -= ux * pull
                ys[ib] -= uy * pull

        # Anisotropic 16:9 gravity (stronger Y pull keeps map wide rather than tall)
        for i in range(n_c):
            if is_uncat_flags[i]:
                xs[i] += (420.0 - xs[i]) * 0.012 * cool
                ys[i] += (160.0 - ys[i]) * 0.020 * cool
            else:
                xs[i] *= 1.0 - 0.009 * cool
                ys[i] *= 1.0 - 0.048 * cool

        # Hard non-overlap separation between all cluster territories
        max_overlap = 0.0
        for i in range(n_c):
            xi = xs[i]
            yi = ys[i]
            ri = radii[i] + cluster_gap
            for j in range(i + 1, n_c):
                dx = xs[j] - xi
                dy = ys[j] - yi
                min_d = ri + radii[j]
                dist_sq = dx * dx + dy * dy
                if dist_sq < min_d * min_d:
                    if dist_sq < 1e-12:
                        ang = (i * 1.7 + j * 2.9)
                        ux, uy = math.cos(ang), math.sin(ang)
                        dist = 0.0
                    else:
                        dist = math.sqrt(dist_sq)
                        ux, uy = dx / dist, dy / dist
                    gap = min_d - dist
                    if gap > max_overlap:
                        max_overlap = gap
                    overlap = gap * 0.54
                    xi -= ux * overlap
                    yi -= uy * overlap
                    xs[j] += ux * overlap
                    ys[j] += uy * overlap
            xs[i] = xi
            ys[i] = yi

        if step >= 55 and max_overlap < 0.25:
            break

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

    # Spatial graph-coloring after layout so neighboring clusters never share a color
    _assign_spatial_cluster_colors(clusters, cluster_edges)

    # 3. Deterministic intra-cluster placement (high score closer to core, semantic angle, O(M) grid relaxation)
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

        _relax_cluster_points_grid(c.x, c.y, max_r, pts_x, pts_y, min_dist=20.0, iterations=24)

        member_xy: list[tuple[float, float]] = []
        for it, px, py in zip(members, pts_x, pts_y):
            rx, ry = round(px, 1), round(py, 1)
            node_coords[it.id] = (rx, ry)
            member_xy.append((rx, ry))

        c.hull = _build_cluster_hull(c.x, c.y, c.radius, member_xy)

    # 4. Place client nodes cleanly OUTSIDE all cluster hulls/territories near their linked materials
    client_coords: dict[str, tuple[float, float]] = {}
    placed_clients: list[tuple[float, float]] = []
    # Precompute outer radius per cluster (hull extent + visual breathing margin)
    cluster_bounds: list[tuple[float, float, float]] = []
    for c in clusters:
        hull_r = max(
            (math.hypot(hx - c.x, hy - c.y) for hx, hy in c.hull),
            default=c.radius,
        )
        cluster_bounds.append((c.x, c.y, max(c.radius, hull_r) + 26.0))

    outer_ring_r = max(
        (math.hypot(cx, cy) + cr for cx, cy, cr in cluster_bounds),
        default=120.0,
    ) + 28.0

    for c_idx, cid in enumerate(sorted(client_links.keys())):
        mids = [m for m in client_links[cid] if m in node_coords]
        if mids:
            cx = sum(node_coords[m][0] for m in mids) / len(mids)
            cy = sum(node_coords[m][1] for m in mids) / len(mids)
            norm = math.hypot(cx, cy)
            if norm > 1e-6:
                px = cx + (cx / norm) * 28.0
                py = cy + (cy / norm) * 28.0
            else:
                ang = c_idx * golden
                px = cx + 28.0 * math.cos(ang)
                py = cy + 28.0 * math.sin(ang)
        else:
            ang = c_idx * golden
            px = outer_ring_r * math.cos(ang)
            py = outer_ring_r * math.sin(ang)

        # Iteratively push outside every cluster territory and away from sibling clients
        for step in range(48):
            pushed = False
            for bx, by, br in cluster_bounds:
                dx = px - bx
                dy = py - by
                d = math.hypot(dx, dy)
                if d < br:
                    if d < 1e-6:
                        ang = c_idx * golden + step * 0.7
                        ux, uy = math.cos(ang), math.sin(ang)
                    else:
                        ux, uy = dx / d, dy / d
                    px = bx + ux * (br + 4.0)
                    py = by + uy * (br + 4.0)
                    pushed = True

            for ox, oy in placed_clients:
                dx = px - ox
                dy = py - oy
                d = math.hypot(dx, dy)
                if d < 36.0:
                    if d < 1e-6:
                        ang = c_idx * 1.9 + 0.4
                        ux, uy = math.cos(ang), math.sin(ang)
                    else:
                        ux, uy = dx / d, dy / d
                    px += ux * (36.0 - d + 2.0)
                    py += uy * (36.0 - d + 2.0)
                    pushed = True

            if not pushed:
                break

        # Final safety check: if trapped between tightly packed hulls, step onto outer perimeter in same direction
        if any(math.hypot(px - bx, py - by) < br - 2.0 for bx, by, br in cluster_bounds):
            ang = math.atan2(py, px) if (abs(px) > 1e-6 or abs(py) > 1e-6) else c_idx * golden
            px = outer_ring_r * math.cos(ang)
            py = outer_ring_r * math.sin(ang)
            for ox, oy in placed_clients:
                if math.hypot(px - ox, py - oy) < 36.0:
                    ang += 0.25
                    px = outer_ring_r * math.cos(ang)
                    py = outer_ring_r * math.sin(ang)

        rx, ry = round(px, 1), round(py, 1)
        client_coords[cid] = (rx, ry)
        placed_clients.append((rx, ry))

    return node_coords, client_coords
