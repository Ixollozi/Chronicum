"""Unified composer: URL / note / search, plus entry modes (knowledge / task / rule)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models import EntryKind, ProcessingStatus
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

ComposerMode = Literal["auto", "ask", "knowledge", "task", "rule"]
Intent = Literal["url", "note", "search", "refuse", "task", "rule"]

_MODE_ALIASES = {
    "auto": "auto",
    "ask": "ask",
    "search": "ask",
    "спросить": "ask",
    "knowledge": "knowledge",
    "знание": "knowledge",
    "note": "knowledge",
    "task": "task",
    "задача": "task",
    "rule": "rule",
    "правило": "rule",
}

_SLASH_PREFIX = re.compile(
    r"^\s*/(задача|task|правило|rule|знание|knowledge|спросить|ask|search)\s+",
    re.I,
)


@dataclass
class ComposerResult:
    intent: Intent
    message: str
    material_id: str | None = None
    answer: str | None = None
    sources: list | None = None


def normalize_mode(mode: str | None) -> ComposerMode:
    key = (mode or "auto").strip().lower()
    return _MODE_ALIASES.get(key, "auto")  # type: ignore[return-value]


def strip_mode_prefix(text: str) -> tuple[str, ComposerMode | None]:
    """Optional /задача … prefix when mode is auto."""
    m = _SLASH_PREFIX.match(text or "")
    if not m:
        return (text or "").strip(), None
    return text[m.end() :].strip(), normalize_mode(m.group(1))


def classify_intent(text: str) -> Intent:
    """Heuristic intent (URL / search / note) for auto mode."""
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


def _entry_kind_for_mode(mode: ComposerMode) -> str:
    if mode == "task":
        return EntryKind.TASK.value
    if mode == "rule":
        return EntryKind.RULE.value
    return EntryKind.KNOWLEDGE.value


async def _enqueue_url(session: AsyncSession, url: str, *, entry_kind: str) -> ComposerResult:
    try:
        source_type = classify_url(url)
    except UnsupportedSourceError as e:
        raise ValueError(str(e)) from e
    material = await create_material(
        session,
        source_type=source_type,
        source_url=url,
        content_hash=url_sha256(url),
        entry_kind=entry_kind,
    )
    if not (material.summary and material.processing_status == ProcessingStatus.DONE.value):
        await enqueue_material(material.id)
    label = {
        EntryKind.TASK.value: "задача",
        EntryKind.RULE.value: "правило",
    }.get(entry_kind, source_type)
    return ComposerResult(
        intent="task" if entry_kind == EntryKind.TASK.value else ("rule" if entry_kind == EntryKind.RULE.value else "url"),
        message=f"{label} принят в очередь",
        material_id=material.id,
    )


async def _enqueue_note(session: AsyncSession, text: str, *, entry_kind: str) -> ComposerResult:
    material = await create_material(
        session,
        source_type="note",
        note_text=text,
        content_hash=text_sha256(f"{entry_kind}:{text}"),
        title=text[:80],
        entry_kind=entry_kind,
    )
    if not (material.summary and material.processing_status == ProcessingStatus.DONE.value):
        await enqueue_material(material.id)
    if entry_kind == EntryKind.TASK.value:
        return ComposerResult(intent="task", message="Задача принята в очередь", material_id=material.id)
    if entry_kind == EntryKind.RULE.value:
        return ComposerResult(intent="rule", message="Правило принято в очередь", material_id=material.id)
    return ComposerResult(intent="note", message="Заметка принята в очередь", material_id=material.id)


async def run_composer(
    session: AsyncSession,
    text: str,
    *,
    mode: str | None = "auto",
) -> ComposerResult:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty input")

    if is_jailbreak_attempt(text):
        log.info("composer_refused", kind="jailbreak", chars=len(text))
        return ComposerResult(
            intent="refuse",
            message="Отклонено",
            answer=DEFAULT_REFUSE,
            sources=[],
        )

    resolved = normalize_mode(mode)
    body, slash_mode = strip_mode_prefix(text)
    if resolved == "auto" and slash_mode:
        resolved = slash_mode
        text = body
    elif resolved != "auto" and slash_mode:
        # Explicit chip wins; still strip accidental slash prefix
        text = body or text

    if not text:
        raise ValueError("Empty input")

    if resolved == "ask":
        answer, materials = await answer_question(session, text)
        return ComposerResult(
            intent="search",
            message="Ответ по базе",
            answer=answer,
            sources=materials,
        )

    entry_kind = _entry_kind_for_mode(resolved)

    if resolved in {"task", "rule", "knowledge"}:
        url_match = URL_RE.search(text)
        if url_match:
            url = url_match.group(0).rstrip(").,]\"'")
            cleaned = text.strip()
            if cleaned == url or (len(cleaned) < 280 and cleaned.startswith("http")):
                return await _enqueue_url(session, url, entry_kind=entry_kind)
        return await _enqueue_note(session, text, entry_kind=entry_kind)

    # auto
    intent = classify_intent(text)
    if intent == "url":
        m = URL_RE.search(text)
        assert m
        url = m.group(0).rstrip(").,]\"'")
        return await _enqueue_url(session, url, entry_kind=EntryKind.KNOWLEDGE.value)

    if intent == "search":
        answer, materials = await answer_question(session, text)
        return ComposerResult(
            intent="search",
            message="Ответ по базе",
            answer=answer,
            sources=materials,
        )

    return await _enqueue_note(session, text, entry_kind=EntryKind.KNOWLEDGE.value)
