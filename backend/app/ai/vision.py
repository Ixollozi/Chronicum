"""Image description via vision models (Gemini preferred, Groq vision optional)."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)


def _extract_json_obj(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data
    return {}


async def describe_image(path: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    p = Path(path)
    if not p.exists():
        return ""

    if settings.google_ai_api_key.strip():
        try:
            return await _gemini_describe(path, settings)
        except Exception as e:
            log.warning("gemini_vision_failed", error=str(e)[:300])

    if settings.groq_api_key.strip() and settings.groq_api_key.strip().lower() not in {
        "replace_me",
        "changeme",
        "xxx",
    }:
        try:
            return await _groq_describe(path, settings)
        except Exception as e:
            log.warning("groq_vision_failed", error=str(e)[:300])

    return ""


async def analyze_moment_frame(
    path: str,
    *,
    t_sec: float,
    spoken_context: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Structured screenshot analysis for key moments."""
    settings = settings or get_settings()
    prompt = (
        "Ты разбираешь ключевой кадр из видео для личной базы знаний.\n"
        f"Таймкод: {t_sec:.1f}s.\n"
        f"Что говорили рядом:\n{(spoken_context or '(нет)')[:1200]}\n\n"
        "Верни ТОЛЬКО JSON:\n"
        '{"caption":"конкретно что на экране (UI/схема/шаги/текст)","why":"зачем этот кадр полезен",'
        '"when_to_apply":"когда применять","visible_text":"весь читаемый текст с экрана",'
        '"quality":1-5,"junk":false}\n'
        "junk=true если это логотип/титульный экран/заставка/интро/аутро/конец без учебной пользы "
        "или просто пейзаж/размытие без UI и без actionable content.\n"
        "caption и why — конкретные, без воды. Русский."
    )

    raw = ""
    if settings.google_ai_api_key.strip():
        try:
            raw = await _gemini_prompted(path, settings, prompt)
        except Exception as e:
            log.warning("gemini_moment_vision_failed", error=str(e)[:300])
    if not raw and settings.groq_api_key.strip() and settings.groq_api_key.strip().lower() not in {
        "replace_me",
        "changeme",
        "xxx",
    }:
        try:
            raw = await _groq_prompted(path, settings, prompt)
        except Exception as e:
            log.warning("groq_moment_vision_failed", error=str(e)[:300])

    data = _extract_json_obj(raw) if raw else {}
    caption = str(data.get("caption") or "").strip()
    why = str(data.get("why") or "").strip()
    when = str(data.get("when_to_apply") or "").strip()
    visible = str(data.get("visible_text") or "").strip()
    try:
        quality = int(data.get("quality") or 0)
    except (TypeError, ValueError):
        quality = 0
    junk = bool(data.get("junk"))
    junk_hints = ("логотип", "титульн", "заставк", "intro", "outro", "thumbnail", "обложк", "эндкард", "end card")
    blob = f"{caption} {why}".lower()
    if any(h in blob for h in junk_hints) and quality <= 3:
        junk = True
    if not caption and raw and not data:
        caption = raw.strip()[:400]
    if visible and caption and visible.lower() not in caption.lower():
        caption = f"{caption} «{visible[:120]}»".strip()
    return {
        "caption": caption[:400],
        "why": why[:400],
        "when_to_apply": when[:300],
        "visible_text": visible[:500],
        "quality": quality,
        "junk": junk,
    }


async def _gemini_describe(path: str, settings: Settings) -> str:
    return await _gemini_prompted(
        path,
        settings,
        "Describe this image for a personal knowledge base. "
        "Include visible text (transcribe), UI labels, diagrams, and the main idea. "
        "Respond in the language of any visible text, else Russian. Be concise.",
    )


async def _gemini_prompted(path: str, settings: Settings, prompt: str) -> str:
    import google.generativeai as genai

    def _run() -> str:
        genai.configure(api_key=settings.google_ai_api_key)
        model = genai.GenerativeModel(settings.google_ai_model)
        data = Path(path).read_bytes()
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        resp = model.generate_content(
            [
                prompt,
                {"mime_type": mime, "data": data},
            ]
        )
        return (getattr(resp, "text", None) or "").strip()

    return await asyncio.to_thread(_run)


async def _groq_describe(path: str, settings: Settings) -> str:
    return await _groq_prompted(
        path,
        settings,
        "Describe this image for a personal knowledge base. "
        "Include visible text, UI labels, diagrams, and the main idea. "
        "Respond in the language of any visible text, else Russian. Be concise.",
    )


async def _groq_prompted(path: str, settings: Settings, prompt: str) -> str:
    from groq import AsyncGroq

    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    client = AsyncGroq(api_key=settings.groq_api_key)
    model = getattr(settings, "groq_vision_model", None) or "meta-llama/llama-4-scout-17b-16e-instruct"
    resp = await client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
    )
    return (resp.choices[0].message.content or "").strip()
