"""Keyframe / key-moment extraction for video materials (OCR-first, free-local)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import _extract_json, with_llm_fallback
from app.ai.vision import analyze_moment_frame
from app.config import PROJECT_ROOT, get_settings
from app.logging_setup import get_logger
from app.models import Material
from app.services.media import (
    extract_audio,
    extract_best_frame_near,
    extract_frame_at,
    extract_keyframes,
    probe_duration_seconds,
)
from app.services.resources import harvest_resources
from app.services.screen_atlas import (
    AtlasFrame,
    build_screen_atlas,
    load_atlas_for_material,
)

log = get_logger(__name__)

VIDEO_TYPES = {"youtube", "instagram", "tiktok", "video"}
_TS_LINE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:\.(\d+))?\]\s*(.*)$")
_LISTICLE_SPEECH = re.compile(
    r"(?:^|\b)(?:шаг|step)\s*(\d{1,2})\b|#\s*(\d{1,2})\b|"
    r"(?:первый|вторая?|третия?|четвёрт[аыйя]|четверт[аыйя]|пятая?)\s+"
    r"(?:текст|анимац|эффек|способ)|"
    r"(?:first|second|third)\s+(?:text|animation)|"
    r"(?:для\s+)?(\d)\s+текстовых?\s+анимац|"
    r"tutorial\s+for\s+(\d)|"
    r"продолжает?[её]?\s+с\s+втор",
    re.I,
)
_LISTICLE_OCR = re.compile(r"#\s*(\d{1,2})\b|(?:шаг|step)\s*(\d{1,2})\b", re.I)
_MULTI_PART_TUTORIAL = re.compile(
    r"(?:\d)\s+текстовых?\s+анимац|"
    r"(?:три|3)\s+(?:разных\s+)?(?:типа|вида)\s+анимац|"
    r"tutorial\s+(?:for\s+)?\d\s+(?:text|animasi)|"
    r"\d\s+animasi\s+text|"
    r"animasi\s+text\s+(?:kedua|ketiga|kedua|2|3)|"
    r"text\s+animation\s+(?:two|three|2|3)|"
    r"(?:animation|анимаци).{0,40}(?:two|three|второй|третий|kedua|ketiga)",
    re.I,
)
_CTA = re.compile(r"(пиши|лям|подпишись|subscribe|лайк|в описании|ссылка в|пиши слово)", re.I)
_BRANDISH = re.compile(
    r"\b(premiere|davinci|final\s*cut|capcut|envato|envatoelements|storyblocks|pexels|unsplash|"
    r"chatgpt|figma|photoshop|canva|artlist|epidemic|dafont|motionarray|creativemarket|"
    r"fonts\.google)\b",
    re.I,
)

_JUNK_SPEECH = re.compile(
    r"("
    r"подпишись|subscribe|лайк\b|like and|всем привет|приветствую|сегодня мы|всем пока|"
    r"до новых|смотрите до конца|в этом видео|hey guys|what's up|follow me|"
    r"music bed|\bintro\b|\boutro\b|логотип|титульн|заставк|обложк|эндкард|end ?card|"
    r"название игры|title screen|game logo"
    r")",
    re.I,
)
_GOOD_SPEECH = re.compile(
    r"\b("
    r"нажми|открой|выбери|настрой|добавь|удали|скопируй|вставь|перейди|кликни|"
    r"шаг|этап|сначала|затем|потом|например|кнопк|меню|панель|timeline|"
    r"settings|effect|эффект|экспорт|export|капк|capcut|фигм|figma|prompt|промпт|"
    r"overlay|keyframe|opacity|layer|таймлайн|timeline|"
    r"секунд|пиксе|%\d|\d+%|\d+\s*(px|сек)|b-?roll|бирол"
    r")\b",
    re.I,
)


def _moments_dir(material_id: str) -> Path:
    settings = get_settings()
    root = Path(settings.storage_path)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    d = root / "materials" / material_id / "moments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atlas_dir(material_id: str) -> Path:
    settings = get_settings()
    root = Path(settings.storage_path)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root / "materials" / material_id / "atlas"


def parse_timed_segments(raw_text: str | None) -> list[dict]:
    """Parse [m:ss] lines from transcript into {start, end, text}."""
    if not raw_text:
        return []
    segs: list[dict] = []
    for line in raw_text.splitlines():
        m = _TS_LINE.match(line.strip())
        if not m:
            continue
        # Skip OCR lines
        if line.strip().upper().startswith("[OCR"):
            continue
        start = int(m.group(1)) * 60 + int(m.group(2))
        if m.group(3):
            start += int(m.group(3)) / (10 ** len(m.group(3)))
        text = (m.group(4) or "").strip()
        if text:
            segs.append({"start": float(start), "end": float(start) + 2.5, "text": text})
    for i in range(len(segs) - 1):
        segs[i]["end"] = max(segs[i]["start"] + 0.4, float(segs[i + 1]["start"]))
    return segs


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", (text or "").lower())}


def score_segment_value(seg: dict, *, index: int, total: int, duration: float) -> float:
    """How worth a screenshot this spoken beat is."""
    text = (seg.get("text") or "").strip()
    if len(text) < 8:
        return -5.0
    score = 0.0
    if _JUNK_SPEECH.search(text):
        score -= 4.0
    good_hits = _GOOD_SPEECH.findall(text)
    score += min(6.0, len(good_hits) * 1.4)
    score += min(3.0, len(re.findall(r"\d+", text)) * 0.6)
    toks = _tokenize(text)
    score += min(2.0, len(toks) / 12.0)

    start = float(seg.get("start") or 0)
    if duration > 18:
        head = max(2.5, duration * 0.08)
        tail = duration * 0.92
        if start < head:
            score -= 3.5 if not good_hits else 1.0
        if start > tail:
            score -= 3.0 if not good_hits else 0.8
    if index <= 1 and duration > 20:
        score -= 1.2
    if index >= max(0, total - 2) and duration > 20:
        score -= 1.0
    return score


def detect_listicle(raw_text: str | None, segments: list[dict], atlas: list[AtlasFrame]) -> bool:
    blob = raw_text or ""
    speech_hits = len(_LISTICLE_SPEECH.findall(blob))
    ocr_hits = sum(1 for fr in atlas if _LISTICLE_OCR.search(fr.text or ""))
    step_segs = sum(1 for s in segments if _LISTICLE_SPEECH.search(s.get("text") or ""))
    if speech_hits >= 2 or ocr_hits >= 2 or step_segs >= 2:
        return True
    return bool(_MULTI_PART_TUTORIAL.search(blob))


def _spread_speech_picks(
    candidates: list[dict],
    *,
    duration: float,
    max_moments: int = 5,
    existing: list[dict] | None = None,
) -> list[dict]:
    """Pick instructional speech beats spaced across the video timeline."""
    existing = existing or []
    used = [float(p.get("t_sec") or 0) for p in existing]
    if not candidates or duration <= 0:
        return []

    buckets = max(3, min(max_moments, int(duration // 35) + 1))
    width = duration / buckets
    out: list[dict] = []

    for b in range(buckets):
        lo, hi = b * width, (b + 1) * width
        pool = [c for c in candidates if lo <= float(c.get("start") or 0) < hi]
        pool.sort(key=lambda c: float(c.get("_score") or 0), reverse=True)
        for c in pool:
            t = float(c["start"]) + 0.55
            if any(abs(t - u) < 8.0 for u in used):
                continue
            used.append(t)
            text = (c.get("text") or "")[:160]
            out.append(
                {
                    "t_sec": t,
                    "quote": text,
                    "caption": text,
                    "why": "Ключевой фрагмент по речи",
                    "when_to_apply": "",
                }
            )
            break
        if len(existing) + len(out) >= max_moments:
            break
    return out


def is_ocr_junk(text: str, *, t_sec: float = 0.0, duration: float = 0.0) -> bool:
    """Local free junk detector for on-screen text fragments."""
    from app.services.local_ocr import ocr_is_garble, ocr_sanitize

    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return True
    if ocr_is_garble(cleaned) and not ocr_sanitize(cleaned):
        return True
    cleaned = ocr_sanitize(cleaned) or cleaned
    toks = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", cleaned)
    if len(toks) < 2 and not _LISTICLE_OCR.search(cleaned) and not _BRANDISH.search(cleaned):
        return True
    letters = re.sub(r"[^A-Za-zА-Яа-яЁё]+", "", cleaned)
    if (
        len(toks) <= 3
        and letters.isupper()
        and not _LISTICLE_OCR.search(cleaned)
        and not _BRANDISH.search(cleaned)
        and len(cleaned) < 28
    ):
        return True
    if duration > 12 and t_sec >= duration - max(3.0, duration * 0.08):
        if _CTA.search(cleaned) and not _BRANDISH.search(cleaned) and not _LISTICLE_OCR.search(cleaned):
            return True
    if re.fullmatch(r"@[\w.]+", cleaned):
        return True
    return False


def _caption_from_ocr(text: str, max_len: int = 160) -> str:
    from app.services.local_ocr import ocr_sanitize

    cleaned = ocr_sanitize(text or "")
    cleaned = re.sub(r"@[\w.]+$", "", cleaned).strip()
    return cleaned[:max_len]


def _caption_from_speech(quote: str, why: str = "", max_len: int = 160) -> str:
    q = re.sub(r"\s+", " ", (quote or "").strip())
    if q:
        return q[:max_len]
    return ""


def score_atlas_frame(fr: AtlasFrame, *, duration: float, listicle: bool) -> float:
    text = fr.text or ""
    if is_ocr_junk(text, t_sec=fr.t_sec, duration=duration):
        return -10.0
    score = float(fr.score or 0) * 0.01
    toks = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", text)
    score += min(8.0, len(toks) * 0.45)
    if _LISTICLE_OCR.search(text):
        score += 6.0 if listicle else 3.0
    if _BRANDISH.search(text):
        score += 5.5
    if re.search(r"для\s+\w+", text, re.I):
        score += 2.0
    # Prefer mid/content over CTA tail
    if duration > 12 and fr.t_sec > duration * 0.9:
        score -= 4.0
    if duration > 12 and fr.t_sec < duration * 0.06:
        score -= 1.5
    return score


def resolve_timestamp(
    *,
    t_sec: float,
    quote: str,
    caption: str,
    segments: list[dict],
    duration: float,
) -> float:
    """Resolve timestamp primarily by quote→segment match, not LLM guess."""

    def _clamp(t: float) -> float:
        t = max(0.0, float(t))
        if duration > 0:
            lo = 0.0 if duration < 12 else min(2.0, duration * 0.05)
            hi = max(lo + 0.5, duration - 0.4)
            t = min(max(t, lo), hi)
        return t

    if not segments:
        return _clamp(t_sec)

    quote_l = (quote or "").strip().lower()
    caption_l = (caption or "").strip().lower()
    needle = _tokenize(f"{quote} {caption}")

    best_i, best_score = -1, -1.0
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        text_l = text.lower()
        toks = _tokenize(text)
        if not toks:
            continue
        score = 0.0
        if quote_l and len(quote_l) >= 6:
            if quote_l in text_l or text_l in quote_l:
                score += 8.0
            else:
                qtoks = _tokenize(quote)
                if qtoks:
                    score += 5.0 * (len(qtoks & toks) / max(1, len(qtoks)))
        if needle:
            score += 2.2 * (len(needle & toks) / max(1, len(needle)))
        score += 0.25 / (1.0 + abs(float(seg["start"]) - float(t_sec)) / 10.0)
        score += 0.15 * score_segment_value(seg, index=i, total=len(segments), duration=duration)
        if caption_l and any(w in text_l for w in caption_l.split()[:4] if len(w) > 3):
            score += 0.4
        if score > best_score:
            best_score, best_i = score, i

    if best_i < 0:
        return _clamp(t_sec)

    seg = segments[best_i]
    start = float(seg["start"])
    end = float(seg.get("end") or (start + 3.0))
    span = max(0.5, end - start)
    t = start + min(1.6, max(0.55, span * 0.35))
    if best_score < 2.0:
        nearest = min(segments, key=lambda s: abs(float(s["start"]) - float(t_sec)))
        t = float(nearest["start"]) + 0.7
    return _clamp(t)


def context_around(segments: list[dict], t_sec: float, window: float = 10.0) -> str:
    if not segments:
        return ""
    parts = []
    for seg in segments:
        if abs(float(seg["start"]) - t_sec) <= window:
            parts.append(seg["text"])
    if parts:
        return " ".join(parts)
    ordered = sorted(segments, key=lambda s: abs(float(s["start"]) - t_sec))[:3]
    ordered.sort(key=lambda s: float(s["start"]))
    return " ".join(s["text"] for s in ordered)


async def resolve_video_path(material: Material) -> str | None:
    if material.file_path and Path(material.file_path).exists():
        p = material.file_path
        if p.endswith(".mp3"):
            mp4 = str(Path(p).with_suffix(".mp4"))
            if Path(mp4).exists():
                return mp4
            for ext in (".webm", ".mkv", ".mov"):
                alt = str(Path(p).with_suffix(ext))
                if Path(alt).exists():
                    return alt
        return p

    if material.source_type in {"youtube", "instagram", "tiktok"} and material.source_url:
        from app.pipelines.youtube import download_media_url

        label = material.source_type
        try:
            path, _title, _dur = await download_media_url(material.source_url, label=label)
            if path and Path(path).exists():
                log.info("moments_redownloaded", material_id=material.id, path=path)
                return path
        except Exception as e:
            log.warning("moments_redownload_failed", error=str(e)[:300])
    return None


async def _ensure_timed_segments(material: Material, vpath: str) -> list[dict]:
    """Reuse existing timed transcript; re-Whisper only if too sparse."""
    parsed = parse_timed_segments(material.raw_text)
    if len(parsed) >= 3:
        log.info("moments_reuse_transcript", material_id=material.id, segments=len(parsed))
        return parsed

    try:
        from app.ai import get_transcribe_client
        from app.db import SessionLocal

        audio = vpath
        if not str(audio).endswith(".mp3"):
            mp3 = str(Path(vpath).with_suffix(".mp3"))
            if Path(mp3).exists():
                audio = mp3
            else:
                try:
                    audio = await extract_audio(vpath, mp3)
                except Exception:
                    audio = vpath

        try:
            client = get_transcribe_client()
            tr = await client.transcribe(audio, language="auto")
        except RuntimeError:
            from app.ai import get_transcribe_client_pooled

            async with SessionLocal() as session:
                client = await get_transcribe_client_pooled(session)
                tr = await client.transcribe(audio, language="auto")

        if tr.segments:
            from app.services.localize import localize_transcript_to_russian
            from app.services.resources import normalize_asr_glitches

            segs = []
            for s in tr.segments:
                if not (s.text or "").strip():
                    continue
                segs.append(
                    {
                        "start": float(s.start),
                        "end": float(s.end or (s.start + 2.0)),
                        "text": normalize_asr_glitches(s.text.strip()),
                    }
                )
            if tr.text:
                old = material.raw_text or ""
                ocr_idx = old.find("[On-screen")
                suffix = old[ocr_idx:] if ocr_idx >= 0 else ""
                timed = []
                for seg in segs:
                    m, sec = divmod(seg["start"], 60)
                    frac = int(round((seg["start"] - int(seg["start"])) * 10))
                    if frac:
                        timed.append(f"[{int(m)}:{int(sec):02d}.{frac}] {seg['text']}")
                    else:
                        timed.append(f"[{int(m)}:{int(sec):02d}] {seg['text']}")
                speech = "\n".join(timed)
                speech = await localize_transcript_to_russian(
                    speech, language=getattr(tr, "language", None)
                )
                localized_segs = parse_timed_segments(speech)
                if localized_segs:
                    segs = [
                        {
                            "start": float(s["start"]),
                            "end": float(s.get("end") or (s["start"] + 2.0)),
                            "text": s["text"],
                        }
                        for s in localized_segs
                    ]
                material.raw_text = (speech + ("\n\n" + suffix if suffix else "")).strip()
            log.info("moments_retranscribed", material_id=material.id, segments=len(segs))
            return segs
    except Exception as e:
        log.warning("moments_retranscribe_failed", error=str(e)[:300])
    return parsed


def _candidate_segments(segments: list[dict], duration: float, limit: int = 14) -> list[dict]:
    ranked = []
    for i, seg in enumerate(segments):
        sc = score_segment_value(seg, index=i, total=len(segments), duration=duration)
        ranked.append((sc, i, seg))
    ranked.sort(key=lambda x: x[0], reverse=True)
    picked: list[dict] = []
    for sc, _i, seg in ranked:
        if sc < 0.2:
            continue
        if any(abs(float(seg["start"]) - float(p["start"])) < 2.5 for p in picked):
            continue
        picked.append({**seg, "_score": sc})
        if len(picked) >= limit:
            break
    if not picked:
        mid = [x for x in ranked if x[0] > -3]
        for sc, _i, seg in mid[:limit]:
            picked.append({**seg, "_score": sc})
        picked.sort(key=lambda s: float(s["start"]))
    else:
        picked.sort(key=lambda s: float(s["start"]))
    return picked


def _step_num_from_text(text: str) -> int | None:
    m = _LISTICLE_OCR.search(text or "") or _LISTICLE_SPEECH.search(text or "")
    if not m:
        return None
    for g in m.groups():
        if g is not None:
            try:
                return int(g)
            except ValueError:
                continue
    return None


def build_listicle_picks(
    atlas: list[AtlasFrame],
    segments: list[dict],
    *,
    duration: float,
    max_moments: int = 6,
) -> list[dict]:
    """One moment per listicle step / OCR-rich resource frame."""
    scored = [
        (score_atlas_frame(fr, duration=duration, listicle=True), fr)
        for fr in atlas
    ]
    scored = [(sc, fr) for sc, fr in scored if sc > 0]
    scored.sort(key=lambda x: x[0], reverse=True)

    by_step: dict[int, AtlasFrame] = {}
    resource_frames: list[AtlasFrame] = []
    for _sc, fr in scored:
        step = _step_num_from_text(fr.text)
        # Prefer richer on-screen lists over bare «#N» title cards
        brand_heavy = bool(_BRANDISH.search(fr.text or ""))
        url_heavy = bool(re.search(r"\.(com|io|app)\b", fr.text or "", re.I))
        if brand_heavy or url_heavy:
            resource_frames.append(fr)
        if step is not None:
            prev = by_step.get(step)
            if prev is None:
                by_step[step] = fr
            else:
                # Upgrade step frame if new one has more brand/URLs
                prev_rich = bool(_BRANDISH.search(prev.text or ""))
                new_rich = brand_heavy or url_heavy
                if (new_rich and not prev_rich) or (fr.score or 0) > (prev.score or 0) + 15:
                    by_step[step] = fr

    speech_steps: list[tuple[int, dict]] = []
    for seg in segments:
        n = _step_num_from_text(seg.get("text") or "")
        if n is not None:
            speech_steps.append((n, seg))

    picks: list[dict] = []
    used_ts: list[float] = []

    def _add(fr: AtlasFrame, quote: str, why: str, *, min_gap: float = 2.4) -> bool:
        # If close to an existing weaker pick, replace when this frame is richer
        for i, t in enumerate(list(used_ts)):
            if abs(fr.t_sec - t) < min_gap:
                existing = picks[i]
                old_rich = bool(_BRANDISH.search(existing.get("_ocr") or existing.get("caption") or ""))
                new_rich = bool(_BRANDISH.search(fr.text or ""))
                if new_rich and not old_rich:
                    used_ts[i] = fr.t_sec
                    step = _step_num_from_text(fr.text) or _step_num_from_text(quote)
                    picks[i] = {
                        "t_sec": float(fr.t_sec),
                        "quote": quote[:160] or existing.get("quote") or "",
                        "caption": _caption_from_ocr(fr.text),
                        "why": why or existing.get("why") or "",
                        "when_to_apply": f"При шаге {step}" if step else existing.get("when_to_apply") or "",
                        "_atlas_path": fr.path,
                        "_ocr": fr.text,
                    }
                    return True
                return False
        if is_ocr_junk(fr.text, t_sec=fr.t_sec, duration=duration):
            return False
        used_ts.append(fr.t_sec)
        step = _step_num_from_text(fr.text) or _step_num_from_text(quote)
        caption = _caption_from_ocr(fr.text)
        picks.append(
            {
                "t_sec": float(fr.t_sec),
                "quote": quote[:160],
                "caption": caption,
                "why": why or (f"Ключевой экран шага {step}" if step else "Полезный экран с ресурсами"),
                "when_to_apply": f"При шаге {step}" if step else "Когда нужно применить этот приём",
                "_atlas_path": fr.path,
                "_ocr": fr.text,
            }
        )
        return True

    # 1) Resource/brand screens first (highest value for archive)
    for fr in resource_frames:
        _add(fr, "", "Список инструментов / ресурсов на экране", min_gap=2.2)
        if len(picks) >= max_moments:
            break

    # 2) Step-aligned frames
    step_keys = sorted(set([n for n, _ in speech_steps] + list(by_step.keys())))
    for n in step_keys:
        if len(picks) >= max_moments:
            break
        fr = by_step.get(n)
        quote = ""
        for sn, seg in speech_steps:
            if sn == n:
                quote = seg.get("text") or ""
                break
        if fr:
            _add(fr, quote or f"Шаг {n}", f"Экран шага {n}")

    # 3) Remaining high-score OCR
    if len(picks) < min(4, max_moments):
        for _sc, fr in scored:
            _add(fr, "", "Информативный кадр с текстом")
            if len(picks) >= max_moments:
                break

    picks.sort(key=lambda p: float(p["t_sec"]))
    return picks[:max_moments]


def build_ocr_picks(atlas: list[AtlasFrame], *, duration: float, max_moments: int = 5) -> list[dict]:
    from app.services.local_ocr import ocr_caption_weak

    scored = [
        (score_atlas_frame(fr, duration=duration, listicle=False), fr)
        for fr in atlas
    ]
    scored = [(sc, fr) for sc, fr in scored if sc > 1.5 and not ocr_caption_weak(fr.text or "")]
    scored.sort(key=lambda x: x[0], reverse=True)
    picks: list[dict] = []
    used: list[float] = []
    for sc, fr in scored:
        if any(abs(fr.t_sec - u) < 4.0 for u in used):
            continue
        used.append(fr.t_sec)
        picks.append(
            {
                "t_sec": float(fr.t_sec),
                "quote": "",
                "caption": _caption_from_ocr(fr.text),
                "why": "Информативный кадр с текстом на экране",
                "when_to_apply": "",
                "_atlas_path": fr.path,
                "_ocr": fr.text,
            }
        )
        if len(picks) >= max_moments:
            break
    picks.sort(key=lambda p: float(p["t_sec"]))
    return picks


async def build_key_moments(session: AsyncSession, material: Material, video_path: str | None = None) -> Material:
    """Pick moments from OCR atlas (preferred) + speech; grab frames; local junk filter."""
    if material.source_type not in VIDEO_TYPES:
        raise ValueError("Moments only for video / youtube / instagram / tiktok")

    from app.services.key_pool import get_app_settings

    app_cfg = await get_app_settings(session)
    vision_gate = bool(app_cfg.get("moments_vision_gate"))

    vpath = video_path if video_path and Path(video_path).exists() else None
    if not vpath:
        vpath = await resolve_video_path(material)
    if vpath and Path(vpath).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return material
    if vpath and (not material.file_path or not Path(material.file_path).exists()):
        material.file_path = vpath

    duration = float(material.duration_seconds or 0)
    if vpath:
        probed = await probe_duration_seconds(vpath)
        if probed > 0 and (not duration or abs(duration - probed) > 1):
            duration = probed
            material.duration_seconds = duration

    segments = await _ensure_timed_segments(material, vpath) if vpath else parse_timed_segments(material.raw_text)
    atlas_path = _atlas_dir(material.id)
    atlas = load_atlas_for_material(
        raw_text=material.raw_text,
        media_path=material.file_path,
        material_atlas_dir=atlas_path,
    )
    from app.services.local_ocr import ocr_is_garble
    from app.services.screen_atlas import format_timed_ocr_block, merge_speech_with_ocr

    usable_ocr_n = sum(1 for a in atlas if (a.text or "").strip() and not ocr_is_garble(a.text))
    need_rebuild = vpath and (
        not atlas
        or sum(1 for a in atlas if a.path and Path(str(a.path)).exists()) < 2
        or usable_ocr_n < 2
    )
    if need_rebuild and vpath:
        try:
            atlas = await build_screen_atlas(
                vpath,
                atlas_path,
                duration=duration,
                source_type=material.source_type,
                keep_top_n=24,
            )
            material.raw_text = merge_speech_with_ocr(material.raw_text, atlas)
        except Exception as e:
            log.warning("moments_atlas_rebuild_failed", error=str(e)[:300])
    elif atlas:
        # Re-sanitize OCR block even if files reused
        material.raw_text = merge_speech_with_ocr(material.raw_text, atlas)

    listicle = detect_listicle(material.raw_text, segments, atlas)
    candidates = _candidate_segments(segments, duration) if segments else []

    picks: list[dict] = []
    if listicle and atlas:
        picks = build_listicle_picks(atlas, segments, duration=duration, max_moments=6)
        log.info("moments_listicle_mode", material_id=material.id, picks=len(picks))
    elif atlas:
        picks = build_ocr_picks(atlas, duration=duration, max_moments=5)

    # Fill thin / front-loaded OCR with speech highlights spaced across the timeline
    need_speech = len(picks) < 4 or (
        duration >= 90
        and picks
        and max(float(p.get("t_sec") or 0) for p in picks) < duration * 0.35
    )
    if need_speech and candidates:
        spread = _spread_speech_picks(
            candidates,
            duration=duration,
            max_moments=5,
            existing=picks,
        )
        picks.extend(spread)
        # Dedup brand-only OCR duplicates once speech fills gaps
        from app.services.local_ocr import ocr_caption_weak

        if len(picks) > 5:
            cleaned: list[dict] = []
            for p in sorted(picks, key=lambda x: float(x.get("t_sec") or 0)):
                cap = str(p.get("caption") or "")
                if ocr_caption_weak(cap) and len(cleaned) >= 3:
                    continue
                if any(abs(float(p.get("t_sec") or 0) - float(c.get("t_sec") or 0)) < 4 for c in cleaned):
                    continue
                cleaned.append(p)
            picks = cleaned[:5]
        else:
            picks.sort(key=lambda p: float(p.get("t_sec") or 0))
            picks = picks[:5]

    # LLM fallback if still empty
    if not picks:
        feed = candidates or segments
        timed_preview = "\n".join(
            f"[{int(s['start'])//60}:{int(s['start'])%60:02d}] {s['text']}" for s in feed[:40]
        )
        transcript_for_llm = timed_preview or (material.raw_text or "")[:40000]
        harvest = harvest_resources(material.raw_text)
        system = (
            "You pick KEY VIDEO MOMENTS for screenshots in a personal knowledge base.\n"
            "Transcript lines are timed [m:ss]. Set t_sec ONLY from those lines (m*60+ss).\n"
            "quote MUST be a short exact substring of a transcript line.\n"
            "Prefer instructional beats: UI steps, settings, before/after, lists, formulas, tools.\n"
            "Title cards like «#4 музыка» and brand lists ARE good moments (not junk).\n"
            "NEVER pick subscribe CTA / endcard / logo-only frames.\n"
            "Return ONLY JSON:\n"
            '{"moments":[{"t_sec":12.5,"quote":"exact phrase","caption":"expected on-screen",'
            '"why":"why useful","when_to_apply":"when"}]}\n'
            "Rules: 3–5 moments; ≥4s apart; Russian OK."
        )
        user = (
            f"Duration seconds: {duration or 'unknown'}\n"
            f"Title: {material.title}\n"
            f"Summary: {material.summary}\n"
            f"Known screen brands: {', '.join(harvest.tools) or '—'}\n"
            f"Candidate timed transcript (prefer these):\n{transcript_for_llm}"
        )

        async def _pick(client):
            raw = await client.complete_text(system, user)
            data = _extract_json(raw)
            return data.get("moments") or []

        try:
            llm_picks = await with_llm_fallback(session, _pick)
        except Exception as e:
            log.warning("moments_llm_failed", error=str(e)[:200])
            llm_picks = []
        if not isinstance(llm_picks, list):
            llm_picks = []
        if not llm_picks and candidates:
            llm_picks = [
                {
                    "t_sec": float(c["start"]) + 0.7,
                    "quote": c["text"][:160],
                    "caption": "",
                    "why": "Ключевой учебный фрагмент по транскрипту",
                    "when_to_apply": "",
                }
                for c in candidates[:5]
            ]
        picks = llm_picks

    out_dir = _moments_dir(material.id)
    for old in out_dir.glob("*.jpg"):
        try:
            old.unlink()
        except Exception:
            pass
    moments: list[dict] = []

    if not vpath:
        log.warning("moments_no_video_file", material_id=material.id, source=material.source_type)
        for m in picks[:6]:
            moments.append(
                {
                    "t_sec": float(m.get("t_sec") or 0),
                    "caption": str(m.get("caption") or ""),
                    "why": str(m.get("why") or ""),
                    "when_to_apply": str(m.get("when_to_apply") or ""),
                    "image_relpath": None,
                }
            )
        material.key_moments = moments
        await session.commit()
        await session.refresh(material)
        return material

    used_ts: list[float] = []
    min_gap = 4.0 if duration >= 25 else 2.0

    def _accept_t(t: float) -> float | None:
        t = max(0.0, float(t))
        if duration > 0:
            t = min(t, max(0.0, duration - 0.35))
        if any(abs(t - u) < min_gap for u in used_ts):
            return None
        used_ts.append(t)
        return t

    async def _capture(i: int, pick: dict) -> dict | None:
        quote = str(pick.get("quote") or "")
        caption_hint = str(pick.get("caption") or "")
        why = str(pick.get("why") or "")
        when = str(pick.get("when_to_apply") or "")
        ocr_text = str(pick.get("_ocr") or caption_hint)
        atlas_src = pick.get("_atlas_path")

        try:
            t_hint = float(pick.get("t_sec") or 0)
        except (TypeError, ValueError):
            t_hint = 0.0

        # Prefer exact atlas timestamp (no roaming offsets that land on neighbor slides)
        if atlas_src and Path(str(atlas_src)).exists():
            t = _accept_t(t_hint)
            if t is None:
                return None
            fname = f"moment_{i:02d}_{int(t)}.jpg"
            fpath = str(out_dir / fname)
            try:
                shutil.copyfile(str(atlas_src), fpath)
            except Exception:
                ok = await extract_frame_at(vpath, t, fpath)
                if not ok:
                    if used_ts and abs(used_ts[-1] - t) < 0.05:
                        used_ts.pop()
                    return None
            chosen_t = t
        else:
            snapped = resolve_timestamp(
                t_sec=t_hint,
                quote=quote,
                caption=caption_hint,
                segments=segments,
                duration=duration,
            )
            t = _accept_t(snapped)
            if t is None:
                for bump in (min_gap, min_gap * 1.5, min_gap * 2.2):
                    t = _accept_t(snapped + bump)
                    if t is not None:
                        break
            if t is None:
                return None
            fname = f"moment_{i:02d}_{int(t)}.jpg"
            fpath = str(out_dir / fname)
            # Tight offsets only — avoid jumping onto next title card
            ok, chosen_t = await extract_best_frame_near(
                vpath, t, fpath, duration=duration, offsets=[0.0, 0.15, -0.15, 0.35]
            )
            if not ok:
                if used_ts and abs(used_ts[-1] - t) < 0.05:
                    used_ts.pop()
                return None
            if used_ts and abs(used_ts[-1] - t) < 0.05:
                used_ts[-1] = chosen_t
            t = chosen_t

        # Refresh OCR from the actual frame file
        try:
            from app.services.screen_atlas import _ocr_image
            import asyncio

            fresh = await asyncio.to_thread(_ocr_image, fpath)
            if fresh:
                ocr_text = fresh
        except Exception:
            pass

        # Prefer usable OCR; otherwise keep speech caption (do not drop good frames)
        from app.services.local_ocr import ocr_sanitize

        usable_ocr = ocr_sanitize(ocr_text or "")
        from app.services.local_ocr import ocr_caption_weak

        caption = usable_ocr
        if not caption or ocr_caption_weak(caption):
            caption = _caption_from_speech(quote, why) or _caption_from_ocr(caption_hint)
        if not caption or ocr_caption_weak(caption):
            caption = _caption_from_speech(context_around(segments, t), why)

        if not caption:
            try:
                Path(fpath).unlink(missing_ok=True)
            except Exception:
                pass
            if used_ts and abs(used_ts[-1] - t) < 0.05:
                used_ts.pop()
            return None

        moment = {
            "t_sec": round(t, 2),
            "caption": caption[:400],
            "why": why,
            "when_to_apply": when,
            "quote": quote,
            "visible_text": (usable_ocr or "")[:500],
            "image_relpath": f"materials/{material.id}/moments/{fname}",
        }

        # Optional paid vision gate (off by default)
        if vision_gate and not (ocr_text and len(ocr_text) >= 8):
            ctx = context_around(segments, t)
            try:
                analyzed = await analyze_moment_frame(fpath, t_sec=t, spoken_context=ctx or quote)
            except Exception as e:
                log.warning("moment_vision_failed", error=str(e)[:200])
                analyzed = {}
            if analyzed.get("junk") and int(analyzed.get("quality") or 0) <= 2:
                try:
                    Path(fpath).unlink(missing_ok=True)
                except Exception:
                    pass
                if used_ts and abs(used_ts[-1] - t) < 0.05:
                    used_ts.pop()
                return None
            if analyzed.get("caption") and not caption:
                moment["caption"] = analyzed["caption"]
            if analyzed.get("why"):
                moment["why"] = analyzed["why"]
            if analyzed.get("visible_text"):
                moment["visible_text"] = analyzed["visible_text"]

        return moment

    if picks:
        for i, m in enumerate(picks[:7]):
            captured = await _capture(i, m if isinstance(m, dict) else {})
            if captured:
                moments.append(captured)
    else:
        ratios = (0.18, 0.35, 0.52, 0.68, 0.84) if duration > 0 else ()
        for i, ratio in enumerate(ratios):
            captured = await _capture(i, {"t_sec": duration * ratio})
            if captured:
                moments.append(captured)
        if not moments:
            step = 20 if duration <= 0 else max(8, int(duration / 5))
            frames = await extract_keyframes(vpath, str(out_dir), every_seconds=step, max_frames=5)
            for i, fp in enumerate(frames):
                fname = Path(fp).name
                dest = out_dir / fname
                if Path(fp).resolve() != dest.resolve():
                    try:
                        dest.write_bytes(Path(fp).read_bytes())
                    except Exception:
                        pass
                moments.append(
                    {
                        "t_sec": float(i * step),
                        "caption": "",
                        "why": "",
                        "when_to_apply": "",
                        "image_relpath": f"materials/{material.id}/moments/{fname}",
                    }
                )

    saved = sum(1 for m in moments if m.get("image_relpath"))
    log.info(
        "moments_built",
        material_id=material.id,
        total=len(moments),
        with_image=saved,
        segments=len(segments),
        atlas=len(atlas),
        listicle=listicle,
        video=vpath,
    )

    material.key_moments = moments
    await session.commit()
    await session.refresh(material)
    if moments and saved == 0:
        raise RuntimeError(
            "Моменты без картинок: не удалось вытащить кадры из видео. "
            "Проверь ffmpeg (pip install imageio-ffmpeg / winget install Gyan.FFmpeg)."
        )
    return material
