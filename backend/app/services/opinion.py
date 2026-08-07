"""Gemini Google Search grounding — AI opinion on a material."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging_setup import get_logger
from app.models import Material
from app.services.key_pool import acquire_keys, mark_key_result

log = get_logger(__name__)


async def build_opinion(session: AsyncSession, material: Material) -> Material:
    keys = await acquire_keys(session, "google")
    if not keys:
        raise ValueError("Добавь Gemini (Google) ключ в Настройках — мнение требует Google Search grounding")

    settings = get_settings()
    prompt = (
        "Ты независимый аналитик. У тебя есть материал из личной базы знаний. "
        "Используй поиск в интернете, чтобы сверить факты и дать своё мнение.\n\n"
        f"Заголовок: {material.title}\n"
        f"Тип: {material.source_type}\n"
        f"URL: {material.source_url or '—'}\n"
        f"Выжимка: {material.digest or material.summary or ''}\n"
        f"Главная идея: {material.main_idea or ''}\n"
        f"Оценка в базе: {material.usefulness_score}/10 — {material.usefulness_reason or ''}\n"
        f"Ключевые идеи: {material.key_ideas or []}\n\n"
        "Структура ответа (на русском, Markdown — ## заголовки, списки, при коде — fenced blocks с языком):\n"
        "1) Короткий вердикт\n"
        "2) Что подтверждается извне\n"
        "3) С чем стоит быть осторожнее\n"
        "4) Кому это полезно\n"
        "5) Итоговое мнение\n"
        "В конце перечисли найденные источники ссылками, если есть."
    )

    last_err: Exception | None = None
    text = ""
    sources: list[str] = []

    for pk in keys:
        try:
            text, sources = await asyncio.to_thread(_gemini_grounded, pk.api_key, settings.google_ai_model, prompt)
            await mark_key_result(session, pk.id, ok=True)
            break
        except Exception as e:
            last_err = e
            await mark_key_result(session, pk.id, ok=False, error=str(e)[:300])
            log.warning("opinion_key_failed", key_id=pk.id, error=str(e)[:200])
            continue
    else:
        raise RuntimeError(f"Gemini opinion failed: {last_err}")

    material.ai_opinion = text
    material.ai_opinion_sources = sources
    material.ai_opinion_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(material)
    return material


def _gemini_grounded(api_key: str, model_name: str, prompt: str) -> tuple[str, list[str]]:
    import google.generativeai as genai

    genai.configure(api_key=api_key)

    # Gemini 2.x Google Search tool; fall back if unsupported
    tools: list | None
    try:
        tools = [{"google_search": {}}]
        model = genai.GenerativeModel(model_name=model_name, tools=tools)
    except Exception:
        try:
            tools = [{"google_search_retrieval": {}}]
            model = genai.GenerativeModel(model_name=model_name, tools=tools)
        except Exception:
            model = genai.GenerativeModel(model_name=model_name)

    resp = model.generate_content(prompt)
    text = (getattr(resp, "text", None) or "").strip()
    sources: list[str] = []

    try:
        # grounding metadata if present
        cands = getattr(resp, "candidates", None) or []
        for c in cands:
            meta = getattr(c, "grounding_metadata", None) or getattr(c, "groundingMetadata", None)
            if not meta:
                continue
            chunks = getattr(meta, "grounding_chunks", None) or getattr(meta, "groundingChunks", None) or []
            for ch in chunks:
                web = getattr(ch, "web", None)
                if web:
                    uri = getattr(web, "uri", None) or getattr(web, "url", None)
                    title = getattr(web, "title", None) or ""
                    if uri:
                        sources.append(f"{title} {uri}".strip())
            supports = getattr(meta, "search_entry_point", None)
            if supports and hasattr(supports, "rendered_content"):
                pass
    except Exception as e:
        log.warning("grounding_parse_failed", error=str(e)[:200])

    if not text:
        raise RuntimeError("Empty Gemini response")
    return text, sources
