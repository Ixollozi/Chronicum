"""Keyframe / key-moment extraction for video materials."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import _extract_json, with_llm_fallback
from app.ai.vision import analyze_moment_frame, describe_image
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

log = get_logger(__name__)

VIDEO_TYPES = {"youtube", "instagram", "video"}
_TS_LINE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:\.(\d+))?\]\s*(.*)$")

_JUNK_SPEECH = re.compile(
    r"("
    r"подпишись|subscribe|лайк\b|like and|всем привет|приветствую|сегодня мы|всем пока|"
    r"до новых|смотрите до конца|в этом видео|hey guys|what's up|follow me|"
    r"музыка|music bed|\bintro\b|\boutro\b|логотип|титульн|заставк|обложк|эндкард|end ?card|"
    r"название игры|title screen|game logo"
    r")",
    re.I,
)
_GOOD_SPEECH = re.compile(
    r"\b("
    r"нажми|открой|выбери|настрой|добавь|удали|скопируй|вставь|перейди|кликни|"
    r"шаг|этап|сначала|затем|потом|например|кнопк|меню|панель|timeline|timeline|"
    r"settings|effect|эффект|экспорт|export|капк|capcut|фигм|figma|prompt|промпт|"
    r"секунд|пиксе|%\d|\d+%|\d+\s*(px|сек)"
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


def parse_timed_segments(raw_text: str | None) -> list[dict]:
    """Parse [m:ss] lines from transcript into {start, end, text}."""
    if not raw_text:
        return []
    segs: list[dict] = []
    for line in raw_text.splitlines():
        m = _TS_LINE.match(line.strip())
        if not m:
            continue
        start = int(m.group(1)) * 60 + int(m.group(2))
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
    # Numbers / UI-ish tokens
    score += min(3.0, len(re.findall(r"\d+", text)) * 0.6)
    toks = _tokenize(text)
    score += min(2.0, len(toks) / 12.0)

    start = float(seg.get("start") or 0)
    if duration > 18:
        # Avoid cold open / endcard zones unless content is strongly instructional
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
        # Exact / substring quote match is strongest signal
        if quote_l and len(quote_l) >= 6:
            if quote_l in text_l or text_l in quote_l:
                score += 8.0
            else:
                qtoks = _tokenize(quote)
                if qtoks:
                    score += 5.0 * (len(qtoks & toks) / max(1, len(qtoks)))
        if needle:
            score += 2.2 * (len(needle & toks) / max(1, len(needle)))
        # Prefer proximity only as a weak prior when quote is weak
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
    # Prefer a bit into the spoken beat (UI often appears after the voice cue)
    span = max(0.5, end - start)
    t = start + min(1.6, max(0.55, span * 0.35))
    # If match was weak, stay closer to LLM t but still in usable zone
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

    if material.source_type in {"youtube", "instagram"} and material.source_url:
        from app.pipelines.youtube import download_media_url

        label = "instagram" if material.source_type == "instagram" else "youtube"
        try:
            path, _title, _dur = await download_media_url(material.source_url, label=label)
            if path and Path(path).exists():
                log.info("moments_redownloaded", material_id=material.id, path=path)
                return path
        except Exception as e:
            log.warning("moments_redownload_failed", error=str(e)[:300])
    return None


async def _ensure_timed_segments(material: Material, vpath: str) -> list[dict]:
    """Always prefer fresh Whisper float segments when possible."""
    parsed = parse_timed_segments(material.raw_text)

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
            tr = await client.transcribe(audio)
        except RuntimeError:
            from app.ai import get_transcribe_client_pooled

            async with SessionLocal() as session:
                client = await get_transcribe_client_pooled(session)
                tr = await client.transcribe(audio)

        if tr.segments:
            segs = []
            for s in tr.segments:
                if not (s.text or "").strip():
                    continue
                segs.append(
                    {
                        "start": float(s.start),
                        "end": float(s.end or (s.start + 2.0)),
                        "text": s.text.strip(),
                    }
                )
            if tr.text:
                old = material.raw_text or ""
                ocr_idx = old.find("[On-screen text")
                suffix = old[ocr_idx:] if ocr_idx >= 0 else ""
                # Keep float precision in stored transcript
                timed = []
                for seg in segs:
                    m, sec = divmod(seg["start"], 60)
                    frac = int(round((seg["start"] - int(seg["start"])) * 10))
                    if frac:
                        timed.append(f"[{int(m)}:{int(sec):02d}.{frac}] {seg['text']}")
                    else:
                        timed.append(f"[{int(m)}:{int(sec):02d}] {seg['text']}")
                material.raw_text = ("\n".join(timed) + ("\n\n" + suffix if suffix else "")).strip()
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
    # Keep moderately spaced high-value beats
    picked: list[dict] = []
    for sc, _i, seg in ranked:
        if sc < 0.2:
            continue
        if any(abs(float(seg["start"]) - float(p["start"])) < 2.5 for p in picked):
            continue
        picked.append({**seg, "_score": sc})
        if len(picked) >= limit:
            break
    # If everything was filtered, keep top non-catastrophic beats from the middle
    if not picked:
        mid = [x for x in ranked if x[0] > -3]
        for sc, _i, seg in mid[:limit]:
            picked.append({**seg, "_score": sc})
        picked.sort(key=lambda s: float(s["start"]))
    else:
        picked.sort(key=lambda s: float(s["start"]))
    return picked


async def build_key_moments(session: AsyncSession, material: Material, video_path: str | None = None) -> Material:
    """Pick timestamps via Whisper segments + LLM, grab best nearby frames, vision-gate junk."""
    if material.source_type not in VIDEO_TYPES:
        raise ValueError("Moments only for video / youtube / instagram")

    vpath = video_path if video_path and Path(video_path).exists() else None
    if not vpath:
        vpath = await resolve_video_path(material)
    if vpath and (not material.file_path or not Path(material.file_path).exists()):
        material.file_path = vpath

    duration = float(material.duration_seconds or 0)
    if vpath:
        probed = await probe_duration_seconds(vpath)
        if probed > 0 and (not duration or abs(duration - probed) > 1):
            duration = probed
            material.duration_seconds = duration

    segments = await _ensure_timed_segments(material, vpath) if vpath else parse_timed_segments(material.raw_text)
    candidates = _candidate_segments(segments, duration) if segments else []

    # Feed LLM only high-value timed lines → fewer intro/logo picks
    feed = candidates or segments
    timed_preview = "\n".join(
        f"[{int(s['start'])//60}:{int(s['start'])%60:02d}] {s['text']}" for s in feed[:40]
    )
    transcript_for_llm = timed_preview or (material.raw_text or "")[:40000]

    system = (
        "You pick KEY VIDEO MOMENTS for screenshots in a personal knowledge base.\n"
        "Transcript lines are timed [m:ss]. Set t_sec ONLY from those lines (m*60+ss).\n"
        "quote MUST be a short exact substring of a transcript line.\n"
        "Prefer instructional beats: UI steps, settings, before/after, lists, formulas, tools.\n"
        "NEVER pick intro/outro/logo/title-card/subscribe/music/bed scenes.\n"
        "Return ONLY JSON:\n"
        '{"moments":[{"t_sec":12.5,"quote":"exact phrase","caption":"expected on-screen",'
        '"why":"why useful","when_to_apply":"when"}]}\n'
        "Rules: 3–5 moments; ≥4s apart; Russian OK."
    )
    user = (
        f"Duration seconds: {duration or 'unknown'}\n"
        f"Title: {material.title}\n"
        f"Summary: {material.summary}\n"
        f"Candidate timed transcript (prefer these):\n{transcript_for_llm}"
    )

    async def _pick(client):
        raw = await client.complete_text(system, user)
        data = _extract_json(raw)
        return data.get("moments") or []

    picks = await with_llm_fallback(session, _pick)
    if not isinstance(picks, list):
        picks = []

    # Fallback: top candidate segments as moments without LLM fluff
    if not picks and candidates:
        picks = [
            {
                "t_sec": float(c["start"]) + 0.7,
                "quote": c["text"][:160],
                "caption": "",
                "why": "Ключевой учебный фрагмент по транскрипту",
                "when_to_apply": "",
            }
            for c in candidates[:5]
        ]

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

    async def _capture(i: int, t_hint: float, quote: str, caption_hint: str, why: str, when: str) -> dict | None:
        snapped = resolve_timestamp(
            t_sec=t_hint,
            quote=quote,
            caption=caption_hint,
            segments=segments,
            duration=duration,
        )
        t = _accept_t(snapped)
        if t is None:
            # nudge forward to satisfy gap
            for bump in (min_gap, min_gap * 1.5, min_gap * 2.2):
                t = _accept_t(snapped + bump)
                if t is not None:
                    break
        if t is None:
            return None

        fname = f"moment_{i:02d}_{int(t)}.jpg"
        fpath = str(out_dir / fname)
        ok, chosen_t = await extract_best_frame_near(vpath, t, fpath, duration=duration)
        if not ok:
            return None
        # Update accepted time to the frame we actually kept
        if used_ts and abs(used_ts[-1] - t) < 0.05:
            used_ts[-1] = chosen_t
        t = chosen_t

        moment = {
            "t_sec": round(t, 2),
            "caption": caption_hint,
            "why": why,
            "when_to_apply": when,
            "quote": quote,
            "image_relpath": f"materials/{material.id}/moments/{fname}",
        }

        # Vision gate: replace junk logo/title frames
        ctx = context_around(segments, t)
        try:
            analyzed = await analyze_moment_frame(fpath, t_sec=t, spoken_context=ctx or quote)
        except Exception as e:
            log.warning("moment_vision_failed", error=str(e)[:200])
            analyzed = {}

        if analyzed.get("junk") or int(analyzed.get("quality") or 0) <= 2:
            # retry deeper into the beat / next second
            for retry_off in (2.4, 4.0, 5.5):
                t2 = t + retry_off
                if duration > 0 and t2 > duration - 0.4:
                    break
                if any(abs(t2 - u) < 1.2 for u in used_ts[:-1]):
                    continue
                fname2 = f"moment_{i:02d}_{int(t2)}.jpg"
                fpath2 = str(out_dir / fname2)
                ok2, chosen2 = await extract_best_frame_near(
                    vpath, t2, fpath2, duration=duration, offsets=[0.0, 0.8, 1.6, 2.4]
                )
                if not ok2:
                    continue
                ctx2 = context_around(segments, chosen2)
                try:
                    analyzed2 = await analyze_moment_frame(fpath2, t_sec=chosen2, spoken_context=ctx2 or quote)
                except Exception:
                    analyzed2 = {}
                if analyzed2.get("junk") and int(analyzed2.get("quality") or 0) <= 2:
                    try:
                        Path(fpath2).unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                # swap to better frame
                try:
                    Path(fpath).unlink(missing_ok=True)
                except Exception:
                    pass
                fname, fpath, t, analyzed = fname2, fpath2, chosen2, analyzed2
                used_ts[-1] = t
                moment["t_sec"] = round(t, 2)
                moment["image_relpath"] = f"materials/{material.id}/moments/{fname}"
                break

        if analyzed.get("caption"):
            moment["caption"] = analyzed["caption"]
        elif not moment.get("caption"):
            try:
                moment["caption"] = (await describe_image(fpath))[:400]
            except Exception:
                pass
        if analyzed.get("why"):
            moment["why"] = analyzed["why"]
        if analyzed.get("when_to_apply"):
            moment["when_to_apply"] = analyzed["when_to_apply"]
        if analyzed.get("visible_text"):
            moment["visible_text"] = analyzed["visible_text"]
        # Drop if still obvious junk after retries
        if analyzed.get("junk") and int(analyzed.get("quality") or 0) <= 2:
            try:
                Path(fpath).unlink(missing_ok=True)
            except Exception:
                pass
            if used_ts and abs(used_ts[-1] - t) < 0.05:
                used_ts.pop()
            return None
        return moment

    if picks:
        for i, m in enumerate(picks[:6]):
            try:
                raw_t = float(m.get("t_sec") or 0)
            except (TypeError, ValueError):
                raw_t = 0.0
            captured = await _capture(
                i,
                raw_t,
                str(m.get("quote") or ""),
                str(m.get("caption") or ""),
                str(m.get("why") or ""),
                str(m.get("when_to_apply") or ""),
            )
            if captured:
                moments.append(captured)
    else:
        step = 20 if duration <= 0 else max(8, int(duration / 5))
        # sample mid-video to avoid intros
        ratios = (0.18, 0.35, 0.52, 0.68, 0.84) if duration > 0 else ()
        for i, ratio in enumerate(ratios):
            t = duration * ratio
            captured = await _capture(i, t, "", "", "", "")
            if captured:
                moments.append(captured)
        if not moments:
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
        candidates=len(candidates),
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
