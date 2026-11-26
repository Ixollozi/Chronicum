"""Lightweight local + optional Gemini embeddings for hybrid semantic search & graph links."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Sequence

import httpx

from app.config import get_settings
from app.logging_setup import get_logger
from app.search.stemmer import normalize_text, stem_word

log = get_logger(__name__)

LOCAL_EMBED_MODEL = "local-hash-v1"
LOCAL_EMBED_DIM = 256

_WORD_RE = re.compile(r"[0-9a-zа-яё]{2,}")

_EMBED_STOP = {
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "with", "is", "are", "was", "were",
    "this", "that", "these", "those", "from", "into", "about", "not", "but", "can", "will", "just",
    "и", "в", "во", "на", "по", "за", "из", "от", "до", "для", "при", "про", "об", "как", "что",
    "это", "эти", "тот", "та", "те", "или", "но", "же", "ли", "бы", "не", "ни", "а", "у", "с", "со",
    "к", "ко", "о", "его", "её", "их", "мы", "вы", "он", "она", "они", "так", "уже", "еще", "ещё",
}


def _hash_bucket(feature: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    val = int.from_bytes(digest, "little")
    idx = (val >> 1) % dim
    sign = 1.0 if (val & 1) == 0 else -1.0
    return idx, sign


def _l2_normalize(vec: list[float], *, decimals: int = 5) -> list[float]:
    norm_sq = sum(x * x for x in vec)
    if norm_sq <= 1e-12:
        return []
    inv = 1.0 / math.sqrt(norm_sq)
    return [round(x * inv, decimals) for x in vec]


def local_embed_text(text: str, *, dim: int = LOCAL_EMBED_DIM) -> list[float]:
    """
    Deterministic, zero-dependency semantic vector using signed feature hashing over
    stemmed unigrams, bigrams, and subword character n-grams (RU + EN).
    """
    norm = normalize_text(text or "")
    if not norm:
        return []
    words = [w for w in _WORD_RE.findall(norm) if w not in _EMBED_STOP]
    if not words:
        words = _WORD_RE.findall(norm)
    if not words:
        return []

    features: dict[str, float] = {}
    stems: list[str] = []
    for w in words[:1500]:
        s = stem_word(w) or w
        stems.append(s)
        k_stem = f"s:{s}"
        features[k_stem] = features.get(k_stem, 0.0) + 1.0
        if w != s:
            k_word = f"w:{w}"
            features[k_word] = features.get(k_word, 0.0) + 0.45
        if len(w) >= 4:
            padded = f"<{w[:18]}>"
            for i in range(len(padded) - 2):
                k3 = f"c3:{padded[i : i + 3]}"
                features[k3] = features.get(k3, 0.0) + 0.22
            for i in range(len(padded) - 3):
                k4 = f"c4:{padded[i : i + 4]}"
                features[k4] = features.get(k4, 0.0) + 0.28

    for i in range(len(stems) - 1):
        kb = f"b:{stems[i]}_{stems[i + 1]}"
        features[kb] = features.get(kb, 0.0) + 0.65

    vec = [0.0] * dim
    for feat, raw_weight in features.items():
        # Sublinear TF damping so repeated boilerplate does not dominate
        weight = 1.0 + math.log(max(1.0, raw_weight)) if raw_weight > 1.0 else raw_weight
        idx, sign = _hash_bucket(feat, dim)
        vec[idx] += sign * weight

    return _l2_normalize(vec)


async def google_embed_text(
    text: str,
    *,
    api_key: str,
    model: str = "text-embedding-004",
    timeout: float = 15.0,
) -> list[float] | None:
    """Request a dense embedding vector from Gemini's embedContent REST API."""
    cleaned = (text or "").strip()
    if not cleaned or not api_key:
        return None
    model_name = model.removeprefix("models/")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:embedContent"
    payload = {
        "model": f"models/{model_name}",
        "content": {"parts": [{"text": cleaned[:6000]}]},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, params={"key": api_key}, json=payload)
            resp.raise_for_status()
            data = resp.json()
        values = (data.get("embedding") or {}).get("values")
        if not isinstance(values, list) or not values:
            return None
        return _l2_normalize([float(x) for x in values])
    except Exception as exc:
        log.warning("google_embed_failed", model=model_name, error=str(exc)[:200])
        return None


def material_embedding_text(material: Any) -> str:
    """Build weighted canonical text representation of a Material for embedding."""
    parts: list[str] = []
    title = (getattr(material, "title", None) or "").strip()
    if title:
        # Repeat title to give it higher lexical/semantic weight
        parts.append(title)
        parts.append(title)
    summary = (getattr(material, "summary", None) or "").strip()
    if summary:
        parts.append(summary)
    main_idea = (getattr(material, "main_idea", None) or "").strip()
    if main_idea:
        parts.append(main_idea)
    cat = getattr(material, "category", None)
    if cat is not None and getattr(cat, "name", None):
        parts.append(str(cat.name))
    tags = getattr(material, "tags", None) or []
    tag_names = [mt.tag.name for mt in tags if getattr(mt, "tag", None) and getattr(mt.tag, "name", None)]
    if tag_names:
        parts.append(" ".join(tag_names))
    for field in ("tools", "technologies", "key_ideas", "practical_takeaways"):
        vals = getattr(material, field, None) or []
        if isinstance(vals, list) and vals:
            parts.append(" ".join(str(v) for v in vals[:12] if v))
    raw_text = (getattr(material, "raw_text", None) or "").strip()
    if raw_text:
        parts.append(raw_text[:1500])
    return "\n".join(parts).strip()


async def compute_embedding(
    text: str,
    *,
    settings: Any | None = None,
    api_key: str | None = None,
) -> dict[str, Any] | None:
    """
    Compute an embedding payload `{"model": str, "dim": int, "vec": list[float]}`.
    Respects `settings.embedding_provider`:
      - "off": returns None
      - "google": calls Gemini embedContent when a valid Google key is available, falls back to local
      - "local" / "auto": uses deterministic zero-dependency local semantic embeddings
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    cfg = settings or get_settings()
    provider = (getattr(cfg, "embedding_provider", "auto") or "auto").strip().lower()
    if provider == "off":
        return None

    if provider == "google":
        key = (api_key or getattr(cfg, "google_ai_api_key", "") or "").strip()
        if key and not key.startswith("your_"):
            model = (getattr(cfg, "google_embedding_model", "text-embedding-004") or "text-embedding-004").strip()
            vec = await google_embed_text(cleaned, api_key=key, model=model)
            if vec:
                return {"model": f"google:{model}", "dim": len(vec), "vec": vec}

    vec = local_embed_text(cleaned)
    if not vec:
        return None
    return {"model": LOCAL_EMBED_MODEL, "dim": len(vec), "vec": vec}


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    val = dot / math.sqrt(na * nb)
    return max(-1.0, min(1.0, val))


def material_vector_for_model(material: Any, model_name: str) -> list[float]:
    """Return stored embedding vector if its model matches; otherwise compute local fallback."""
    emb = getattr(material, "embedding", None)
    if isinstance(emb, dict) and emb.get("model") == model_name:
        vec = emb.get("vec")
        if isinstance(vec, list) and vec:
            return vec
    if model_name == LOCAL_EMBED_MODEL:
        text = material_embedding_text(material)
        return local_embed_text(text) if text else []
    return []
