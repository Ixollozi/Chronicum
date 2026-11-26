"""Unified composer: URL / note / search, plus entry modes (knowledge / task / rule)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models import EntryKind, ProcessingStatus
from app.search import answer_question
from app.services.processing import create_material, needs_enqueue
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
Intent = Literal["url", "note", "search", "refuse", "task", "rule", "batch"]

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
    already_exists: bool = False
    items: list[dict] = field(default_factory=list)


def normalize_mode(mode: str | None) -> ComposerMode:
    key = (mode or "auto").strip().lower()
    return _MODE_ALIASES.get(key, "auto")  # type: ignore[return-value]


def strip_mode_prefix(text: str) -> tuple[str, ComposerMode | None]:
    """Optional /задача … prefix when mode is auto."""
    m = _SLASH_PREFIX.match(text or "")
    if not m:
        return (text or "").strip(), None
    return text[m.end() :].strip(), normalize_mode(m.group(1))


def extract_urls(text: str) -> list[str]:
    """Unique URLs in order of appearance (tracking tails stripped via hash later)."""
    seen: set[str] = set()
    out: list[str] = []
    for m in URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(").,]\"'")
        key = url_sha256(url)
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def _url_paste_only(text: str, urls: list[str]) -> bool:
    """True when the paste is basically one or more links, not a long note with a URL."""
    if not urls:
        return False
    cleaned = (text or "").strip()
    if len(urls) == 1:
        return cleaned == urls[0] or (len(cleaned) < 280 and cleaned.startswith("http"))
    # Multi-URL: strip URLs and leftover whitespace/punctuation — little prose left
    remainder = cleaned
    for u in urls:
        remainder = remainder.replace(u, " ")
    remainder = re.sub(r"[\s,;|]+", " ", remainder).strip()
    return len(remainder) < 40


def classify_intent(text: str) -> Intent:
    """Heuristic intent (URL / search / note) for auto mode."""
    raw = text.strip()
    if not raw:
        return "note"

    urls = extract_urls(raw)
    if urls and _url_paste_only(raw, urls):
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


async def _maybe_requeue_failed(session: AsyncSession, material, already_exists: bool) -> None:
    if already_exists and material.processing_status == ProcessingStatus.FAILED.value:
        material.processing_status = ProcessingStatus.QUEUED.value
        material.error_message = None
        await session.commit()


async def _enqueue_url(
    session: AsyncSession, url: str, *, entry_kind: str, user_id: str | None = None
) -> ComposerResult:
    try:
        source_type = classify_url(url)
    except UnsupportedSourceError as e:
        raise ValueError(str(e)) from e
    out = await create_material(
        session,
        source_type=source_type,
        source_url=url,
        content_hash=url_sha256(url),
        entry_kind=entry_kind,
        user_id=user_id,
    )
    material = out.material
    will = needs_enqueue(material, out.already_exists)
    if will:
        await _maybe_requeue_failed(session, material, out.already_exists)
        await enqueue_material(material.id)
    label = {
        EntryKind.TASK.value: "задача",
        EntryKind.RULE.value: "правило",
    }.get(entry_kind, source_type)
    if out.already_exists and not will:
        msg = "Уже в архиве"
    elif out.already_exists:
        msg = "Повторно в очереди"
    else:
        msg = f"{label} принят в очередь"
    return ComposerResult(
        intent="task" if entry_kind == EntryKind.TASK.value else ("rule" if entry_kind == EntryKind.RULE.value else "url"),
        message=msg,
        material_id=material.id,
        already_exists=out.already_exists,
        items=[{
            "id": material.id,
            "status": material.processing_status,
            "already_exists": out.already_exists,
        }],
    )


async def _enqueue_urls(
    session: AsyncSession, urls: list[str], *, entry_kind: str, user_id: str | None = None
) -> ComposerResult:
    if len(urls) == 1:
        return await _enqueue_url(session, urls[0], entry_kind=entry_kind, user_id=user_id)

    items: list[dict] = []
    for url in urls:
        try:
            source_type = classify_url(url)
        except UnsupportedSourceError as e:
            raise ValueError(str(e)) from e
        out = await create_material(
            session,
            source_type=source_type,
            source_url=url,
            content_hash=url_sha256(url),
            entry_kind=entry_kind,
            user_id=user_id,
        )
        material = out.material
        will = needs_enqueue(material, out.already_exists)
        if will:
            await _maybe_requeue_failed(session, material, out.already_exists)
            await enqueue_material(material.id)
        items.append({
            "id": material.id,
            "status": material.processing_status,
            "already_exists": out.already_exists,
        })

    existed = sum(1 for it in items if it["already_exists"])
    fresh = len(items) - existed
    msg = f"Принято {len(items)}"
    if existed:
        msg += f" ({existed} уже были)"
    if fresh:
        msg += f", в очереди {fresh}"
    return ComposerResult(
        intent="batch",
        message=msg,
        material_id=items[0]["id"] if items else None,
        already_exists=existed == len(items) and len(items) > 0,
        items=items,
    )


async def _enqueue_note(
    session: AsyncSession, text: str, *, entry_kind: str, user_id: str | None = None
) -> ComposerResult:
    out = await create_material(
        session,
        source_type="note",
        note_text=text,
        content_hash=text_sha256(f"{entry_kind}:{text}"),
        title=text[:80],
        entry_kind=entry_kind,
        user_id=user_id,
    )
    material = out.material
    will = needs_enqueue(material, out.already_exists)
    if will:
        await _maybe_requeue_failed(session, material, out.already_exists)
        await enqueue_material(material.id)
    if out.already_exists and not will:
        msg = "Уже в архиве"
    elif out.already_exists:
        msg = "Повторно в очереди"
    elif entry_kind == EntryKind.TASK.value:
        msg = "Задача принята в очередь"
    elif entry_kind == EntryKind.RULE.value:
        msg = "Правило принято в очередь"
    else:
        msg = "Заметка принята в очередь"
    intent: Intent = "task" if entry_kind == EntryKind.TASK.value else (
        "rule" if entry_kind == EntryKind.RULE.value else "note"
    )
    return ComposerResult(
        intent=intent,
        message=msg,
        material_id=material.id,
        already_exists=out.already_exists,
        items=[{
            "id": material.id,
            "status": material.processing_status,
            "already_exists": out.already_exists,
        }],
    )


async def run_composer(
    session: AsyncSession,
    text: str,
    *,
    mode: str | None = "auto",
    user_id: str | None = None,
) -> ComposerResult:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty input")

    scope = user_id is not None
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
        answer, materials = await answer_question(
            session, text, user_id=user_id, scope_user=scope
        )
        return ComposerResult(
            intent="search",
            message="Ответ по базе",
            answer=answer,
            sources=materials,
        )

    entry_kind = _entry_kind_for_mode(resolved)
    urls = extract_urls(text)

    if resolved in {"task", "rule", "knowledge"}:
        if urls and _url_paste_only(text, urls):
            return await _enqueue_urls(session, urls, entry_kind=entry_kind, user_id=user_id)
        return await _enqueue_note(session, text, entry_kind=entry_kind, user_id=user_id)

    # auto
    intent = classify_intent(text)
    if intent == "url":
        return await _enqueue_urls(
            session, urls, entry_kind=EntryKind.KNOWLEDGE.value, user_id=user_id
        )

    if intent == "search":
        answer, materials = await answer_question(
            session, text, user_id=user_id, scope_user=scope
        )
        return ComposerResult(
            intent="search",
            message="Ответ по базе",
            answer=answer,
            sources=materials,
        )

    return await _enqueue_note(
        session, text, entry_kind=EntryKind.KNOWLEDGE.value, user_id=user_id
    )
