"""Unified composer: URL / note / search, plus a thin jailbreak gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models import ProcessingStatus
from app.search import answer_question
from app.services.processing import create_material
from app.services.queue import enqueue_material
from app.services.sources import UnsupportedSourceError, classify_url
from app.services.storage import text_sha256, url_sha256

log = get_logger(__name__)

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

# Minimal gate: block prompt-injection / jailbreak only.
# Songs, code dumps, drafts, random notes — all allowed into the archive.
JAILBREAK_RE = re.compile(
    r"(?is)"
    r"("
    r"игнорируй\s+(все\s+)?(предыдущие|вышестоящие|системные)?\s*(правила|инструкции|промпт)"
    r"|забудь\s+(все\s+)?(инструкции|правила|промпт)"
    r"|ignore\s+(all\s+)?(previous|prior|above|system)\s+(instructions?|prompts?|rules?)"
    r"|disregard\s+(all\s+)?(previous|prior|system)\s+(instructions?|prompts?|rules?)"
    r"|jailbreak"
    r"|dan\s+mode"
    r"|developer\s+mode\b"
    r"|do\s+anything\s+now"
    r"|ты\s+теперь\s+(ии|бот|ассистент|dan)\b"
    r"|you\s+are\s+now\s+(dan|unrestricted|jailbroken)\b"
    r"|act\s+as\s+if\s+you\s+have\s+no\s+(restrictions|rules|limits)"
    r"|reveal\s+(your\s+)?(system\s+)?prompt"
    r"|покажи\s+(свой\s+)?системн(ый|ые)\s+промпт"
    r"|открой\s+системн"
    r"|system\s*prompt\s*:\s*"
    r")"
)

DEFAULT_REFUSE = (
    "Похоже на попытку взломать промпт — такое Chronicum не принимает. "
    "Кинь материал в архив или спроси по базе."
)

Intent = Literal["url", "note", "search", "refuse"]


@dataclass
class ComposerResult:
    intent: Intent
    message: str
    material_id: str | None = None
    answer: str | None = None
    sources: list | None = None


def classify_intent(text: str) -> Intent:
    """Heuristic intent (URL / search / note)."""
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

    if re.match(r"^про\s+\S+", lower) and len(raw) < 100:
        return "search"

    return "note"


def is_jailbreak_attempt(text: str) -> bool:
    return bool(JAILBREAK_RE.search(text.strip()))


async def run_composer(session: AsyncSession, text: str) -> ComposerResult:
    text = text.strip()
    if not text:
        raise ValueError("Empty input")

    # Thin safety net only — never block songs, code, drafts, offbeat notes.
    if is_jailbreak_attempt(text):
        log.info("composer_refused", kind="jailbreak", chars=len(text))
        return ComposerResult(
            intent="refuse",
            message="Отклонено",
            answer=DEFAULT_REFUSE,
            sources=[],
        )

    intent = classify_intent(text)

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
