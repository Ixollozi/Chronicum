from __future__ import annotations

from typing import Any

SUMMARY_MAX = 280
TAG_CAP = 200

SUMMARY_FIELDS = (
    "id",
    "title",
    "source_type",
    "entry_kind",
    "source_url",
    "summary",
    "main_idea",
    "category",
    "tags",
    "usefulness_score",
    "processing_status",
    "processing_stage",
    "trust_level",
    "analysis_confidence",
)

CARD_EXTRA_FIELDS = (
    "digest",
    "key_ideas",
    "practical_takeaways",
    "tools",
    "technologies",
    "links",
    "code_blocks",
    "usefulness_reason",
    "detailed_explanation",
    "objective_evaluation",
    "ai_opinion",
    "error_message",
    "duration_seconds",
    "has_audio",
    "key_moments",
)

TEACH_FIELDS = (
    "id",
    "title",
    "trust_level",
    "analysis_confidence",
    "summary",
    "main_idea",
    "digest",
    "detailed_explanation",
    "tools",
    "links",
    "key_moments",
    "code_blocks",
    "source_url",
)

FULL_DROP = ("usage_tokens_in", "usage_tokens_out", "usage_audio_seconds")


def clip(value: Any, limit: int = SUMMARY_MAX) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def list_card(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "score": item.get("usefulness_score"),
        "status": item.get("processing_status"),
        "category": item.get("category"),
        "tags": item.get("tags") or [],
        "source_type": item.get("source_type"),
        "entry_kind": item.get("entry_kind") or "knowledge",
        "summary": clip(item.get("summary") or item.get("main_idea")),
    }


def search_payload(data: dict[str, Any]) -> dict[str, Any]:
    sources = [list_card(s) for s in data.get("sources") or []]
    return {"answer": data.get("answer") or "", "sources": sources}


def project_material(detail: dict[str, Any], level: str) -> dict[str, Any]:
    level = (level or "card").lower().strip()
    if level not in {"summary", "card", "teach", "full"}:
        raise ValueError("level must be summary, card, teach, or full")

    if level == "teach":
        out = {k: detail.get(k) for k in TEACH_FIELDS}
        out["summary"] = clip(out.get("summary"), 600)
        out["main_idea"] = clip(out.get("main_idea"), 600)
        return out

    out: dict[str, Any] = {k: detail.get(k) for k in SUMMARY_FIELDS}
    out["summary"] = clip(out.get("summary"), 600 if level != "summary" else SUMMARY_MAX)
    out["main_idea"] = clip(out.get("main_idea"), 600 if level != "summary" else SUMMARY_MAX)

    if level == "summary":
        return out

    for key in CARD_EXTRA_FIELDS:
        if key in detail:
            out[key] = detail.get(key)

    if level == "full":
        for key, value in detail.items():
            if key in FULL_DROP or key in out:
                continue
            out[key] = value
        out["raw_text"] = detail.get("raw_text")
    return out


def taxonomy_payload(categories: list[Any], tags: list[Any]) -> dict[str, Any]:
    tag_list = tags[:TAG_CAP]
    return {
        "categories": categories,
        "tags": tag_list,
        "tags_truncated": len(tags) > TAG_CAP,
    }
