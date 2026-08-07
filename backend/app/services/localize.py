"""Localize ASR transcript into Russian for storage / UI (source speech may be any language)."""

from __future__ import annotations

import re

from app.logging_setup import get_logger

log = get_logger(__name__)

_TS_LINE = re.compile(r"^(\[(\d{1,3}):(\d{2})(?:\.(\d+))?\])\s*(.*)$")
_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")

_RU_LANGS = {"ru", "rus", "russian"}


def looks_like_russian(text: str) -> bool:
    """True when speech body is predominantly Cyrillic."""
    body = "\n".join(
        (m.group(5) or "") for line in (text or "").splitlines() if (m := _TS_LINE.match(line.strip()))
    ) or (text or "")
    cyr = len(_CYR.findall(body))
    lat = len(_LAT.findall(body))
    if cyr + lat < 24:
        # Short / sparse — treat as already OK if any Cyrillic or empty
        return cyr >= lat
    return cyr / (cyr + lat) >= 0.45


def needs_russian_localization(text: str, *, language: str | None = None) -> bool:
    lang = (language or "").strip().lower()
    if lang in _RU_LANGS:
        return False
    if lang and lang not in {"auto", "unknown", ""}:
        return True
    return not looks_like_russian(text)


def _chunk_lines(lines: list[str], *, max_chars: int = 4500) -> list[list[str]]:
    chunks: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for line in lines:
        add = len(line) + 1
        if cur and size + add > max_chars:
            chunks.append(cur)
            cur = []
            size = 0
        cur.append(line)
        size += add
    if cur:
        chunks.append(cur)
    return chunks


def _parse_translated_lines(raw: str, expected_ts: list[str]) -> list[str] | None:
    """Map model output back onto original timestamps (by order)."""
    out_texts: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = _TS_LINE.match(s)
        if m:
            out_texts.append((m.group(5) or "").strip())
        else:
            out_texts.append(s)
    if len(out_texts) != len(expected_ts):
        return None
    return [f"{ts} {body}".rstrip() if body else ts for ts, body in zip(expected_ts, out_texts)]


async def localize_transcript_to_russian(
    text: str,
    *,
    language: str | None = None,
    session=None,
) -> str:
    """
    If transcript is not Russian, translate timed lines to Russian via LLM.
    Keeps [m:ss] prefixes; preserves CapCut / overlay / keyframe-style UI terms.
    """
    cleaned = (text or "").strip()
    if not cleaned or not needs_russian_localization(cleaned, language=language):
        return cleaned

    speech_lines = [ln for ln in cleaned.splitlines() if _TS_LINE.match(ln.strip())]
    other_lines = [ln for ln in cleaned.splitlines() if not _TS_LINE.match(ln.strip())]
    if not speech_lines:
        # Untimed blob — translate as a whole block
        speech_lines = [cleaned]
        other_lines = []

    system = (
        "Ты переводишь субтитры/транскрипт видео на русский для личной базы знаний.\n"
        "Правила:\n"
        "- Сохрани те же таймкоды [m:ss] в начале каждой строки, по одной строке на входную.\n"
        "- Переведи смысл на естественный русский.\n"
        "- Названия продуктов и UI-термины оставь латиницей как есть: "
        "CapCut, overlay, keyframe, opacity, layer, timeline, splice, blend, outline, font.\n"
        "- Не добавляй пояснений, заголовков и кавычек вокруг всего текста.\n"
        "- Выведи ТОЛЬКО строки транскрипта."
    )

    translated: list[str] = []
    for chunk in _chunk_lines(speech_lines):
        stamps = []
        for ln in chunk:
            m = _TS_LINE.match(ln.strip())
            stamps.append(m.group(1) if m else "")
        user = "Переведи на русский:\n\n" + "\n".join(chunk)

        async def _run(client, _system=system, _user=user):
            return await client.complete_text(_system, _user)

        try:
            if session is not None:
                from app.ai import with_llm_fallback

                raw_out = await with_llm_fallback(session, _run)
            else:
                from app.ai import get_llm_client

                raw_out = await get_llm_client().complete_text(system, user)
        except Exception as e:
            log.warning("transcript_localize_failed", error=str(e)[:300])
            return cleaned

        mapped = _parse_translated_lines(raw_out, stamps)
        if not mapped:
            # Fallback: keep timestamps, attach raw line bodies in order if counts drift
            bodies = [ln.strip() for ln in raw_out.splitlines() if ln.strip()]
            if len(bodies) == len(chunk):
                mapped = []
                for ts, body in zip(stamps, bodies):
                    m = _TS_LINE.match(body)
                    text_body = (m.group(5) if m else body).strip()
                    mapped.append(f"{ts} {text_body}".rstrip() if ts else text_body)
            else:
                log.warning(
                    "transcript_localize_mismatch",
                    expected=len(chunk),
                    got=len(bodies),
                )
                return cleaned
        translated.extend(mapped)

    # Drop blank non-speech separators; keep OCR blocks etc. after speech
    other = [ln for ln in other_lines if ln.strip()]
    if other:
        return "\n".join(translated + [""] + other).strip()
    return "\n".join(translated).strip()
