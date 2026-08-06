"""Unified composer: decide whether text is a URL, note, or question."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.search import answer_question
from app.services.processing import create_material
from app.services.queue import enqueue_material
from app.services.sources import UnsupportedSourceError, classify_url
from app.services.storage import text_sha256, url_sha256
from app.models import ProcessingStatus

URL_RE = re.compile(r"https?://\S+", re.I)
QUESTION_START = (
    "как ",
    "что ",
    "зачем ",
    "почему ",
    "где ",
    "когда ",
    "какой ",
    "какая ",
    "какие ",
    "чем ",
    "кто ",
    "можно ли",
    "есть ли",
    "найди",
    "найти",
    "покажи",
    "расскажи",
    "сравни",
    "where ",
    "what ",
    "how ",
    "why ",
    "when ",
    "who ",
    "search",
    "find ",
)

Intent = Literal["url", "note", "search"]


@dataclass
class ComposerResult:
    intent: Intent
    message: str
    material_id: str | None = None
    answer: str | None = None
    sources: list | None = None


def classify_intent(text: str) -> Intent | None:
    """Return intent or None if ambiguous (needs LLM)."""
    raw = text.strip()
    if not raw:
        return "note"

    url_match = URL_RE.search(raw)
    if url_match:
        cleaned = raw.strip()
        url = url_match.group(0).rstrip(").,]\"'")
        if cleaned == url or (len(cleaned) < 280 and cleaned.startswith("http")):
            return "url"

    lower = raw.lower()
    if raw.endswith("?") or lower.startswith(QUESTION_START):
        return "search"

    # "про X" as a short lookup (not "просто…")
    if re.match(r"^про\s+\S+", lower) and len(raw) < 100:
        return "search"

    # Declarative text / dumps → save as note (question markers already caught)
    return "note"


async def run_composer(session: AsyncSession, text: str) -> ComposerResult:
    text = text.strip()
    if not text:
        raise ValueError("Empty input")

    intent = classify_intent(text)
    assert intent is not None

    if intent == "url":
        m = URL_RE.search(text)
        assert m
        url = m.group(0).rstrip(").,]\"'")
        try:
            source_type = classify_url(url)
        except UnsupportedSourceError as e:
            raise ValueError(str(e)) from e
        material = await create_material(
            session,
            source_type=source_type,
            source_url=url,
            content_hash=url_sha256(url),
        )
        if not (material.summary and material.processing_status == ProcessingStatus.DONE.value):
            await enqueue_material(material.id)
        return ComposerResult(
            intent="url",
            message=f"{source_type} принят в очередь",
            material_id=material.id,
        )

    if intent == "search":
        answer, materials = await answer_question(session, text)
        return ComposerResult(
            intent="search",
            message="Ответ по базе",
            answer=answer,
            sources=materials,
        )

    # note
    material = await create_material(
        session,
        source_type="note",
        note_text=text,
        content_hash=text_sha256(text),
        title=text[:80],
    )
    if not (material.summary and material.processing_status == ProcessingStatus.DONE.value):
        await enqueue_material(material.id)
    return ComposerResult(
        intent="note",
        message="Заметка принята в очередь",
        material_id=material.id,
    )
