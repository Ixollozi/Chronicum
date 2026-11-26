from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from app.ai import get_transcribe_client
from app.config import Settings, get_settings
from app.logging_setup import get_logger
from app.pipelines.article import extract_article
from app.pipelines.image import extract_image_text
from app.pipelines.pdf import extract_pdf_text
from app.pipelines.youtube import download_media_bundle, fetch_youtube_captions
from app.services.media import (
    extract_audio,
    extract_audio_segment,
    extract_keyframes,
    probe_duration_seconds,
)
from app.services.resources import (
    caption_suggests_english,
    is_weak_speech,
    normalize_asr_glitches,
    speech_body_text,
)
from app.services.screen_atlas import build_screen_atlas, format_timed_ocr_block

log = get_logger(__name__)


async def _transcribe_social_audio(
    client,
    audio_path: str,
    *,
    caption_hint: str = "",
    session=None,
):
    """Whisper with EN retry on hallucination / Latin caption; skip localize on weak speech."""
    from app.services.localize import localize_transcript_to_russian

    prefer_en = caption_suggests_english(caption_hint)
    first_lang = "en" if prefer_en else "auto"
    tr = await client.transcribe(audio_path, language=first_lang)
    text = normalize_asr_glitches(tr.text)

    if is_weak_speech(text, min_chars=8) and first_lang != "en":
        log.info("asr_retry_en", reason="weak_or_hallucination")
        tr2 = await client.transcribe(audio_path, language="en")
        text2 = normalize_asr_glitches(tr2.text)
        if not is_weak_speech(text2, min_chars=8) or len(speech_body_text(text2)) > len(speech_body_text(text)):
            tr, text = tr2, text2
    elif is_weak_speech(text, min_chars=8) and first_lang == "en":
        # Caption looked EN but still weak — one auto retry
        log.info("asr_retry_auto", reason="en_still_weak")
        tr2 = await client.transcribe(audio_path, language="auto")
        text2 = normalize_asr_glitches(tr2.text)
        if not is_weak_speech(text2, min_chars=8) and len(speech_body_text(text2)) > len(speech_body_text(text)):
            tr, text = tr2, text2

    if is_weak_speech(text, min_chars=8):
        # Drop hallucination leftover so carding sees empty speech
        return tr, "", True

    text = await localize_transcript_to_russian(
        text, language=getattr(tr, "language", None), session=session
    )
    return tr, text, False


def _persist_slides(paths: list[str], dest_dir: str) -> list[str]:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for i, raw in enumerate(paths, 1):
        src = Path(raw)
        if not src.exists():
            continue
        suffix = src.suffix.lower() if src.suffix else ".jpg"
        target = dest / f"slide_{i:02d}{suffix}"
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        out.append(str(target))
    return out


async def _whisper_client(settings: Settings, session=None):
    from app.ai import PooledTranscribeClient

    if settings.transcribe_provider != "groq":
        return get_transcribe_client(settings)
    return PooledTranscribeClient(settings, session=session)


async def ocr_frames(paths: list[str]) -> str:
    def _ocr() -> str:
        from app.services.local_ocr import ocr_image_file

        parts = []
        for p in paths:
            try:
                text = ocr_image_file(p)
                if text:
                    parts.append(text)
            except Exception as e:
                log.warning("frame_ocr_failed", path=p, error=str(e))
        return "\n".join(parts)

    return await asyncio.to_thread(_ocr)


_VIDEO_FILE_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi"}


async def _extract_audio_safe(video_path: str) -> str:
    p = Path(video_path)
    if p.suffix.lower() == ".mp3":
        return video_path
    audio_path = str(p.with_suffix(".mp3"))
    try:
        return await extract_audio(video_path, audio_path)
    except Exception:
        return video_path


async def _extract_video_pipeline(
    *,
    video_paths: list[str],
    title: str | None,
    duration: float | None,
    source_type: str,
    stage: str,
    settings: Settings,
    atlas_dir: str | None,
    session,
    extra_block: str = "",
    caption_hint: str = "",
    on_progress=None,
) -> tuple[str, str | None, float | None, float, str | None]:
    if not video_paths:
        return extra_block or (title or ""), title, None, 0.0, None

    if len(video_paths) == 1:
        dur = float(duration) if duration and duration > 0 else await probe_duration_seconds(video_paths[0])
        per_durations = [dur]
        total_duration = dur if dur > 0 else None
    else:
        per_durations = [await probe_duration_seconds(vp) for vp in video_paths]
        probed_total = sum(d for d in per_durations if d > 0)
        total_duration = probed_total if probed_total > 0 else (float(duration) if duration and duration > 0 else None)

    threshold_sec = settings.video_preview_threshold_min * 60
    is_long = (total_duration or 0.0) > threshold_sec
    do_preview = stage == "preview" and is_long and bool(total_duration)

    if on_progress is not None:
        await on_progress("asr")
    client = await _whisper_client(settings, session=session)
    primary_video = video_paths[0]
    hint = caption_hint or (title or "")

    if do_preview and total_duration:
        preview_budget = min(
            float(total_duration),
            max(30.0, float(total_duration) * settings.video_preview_fraction),
        )
        remaining = preview_budget
        chunks: list[str] = []
        audio_sec = 0.0
        speech_weak_any = False

        for vi, vp in enumerate(video_paths, 1):
            if remaining <= 0.5:
                break
            vdur = per_durations[vi - 1]
            seg_dur = min(vdur, remaining) if vdur > 0 else remaining
            seg_path = str(Path(vp).with_name(Path(vp).stem + "_preview.mp3"))
            if vdur > 0 and seg_dur >= vdur - 0.5:
                audio_for_asr = await _extract_audio_safe(vp)
            else:
                try:
                    audio_for_asr = await extract_audio_segment(vp, 0, seg_dur, seg_path)
                except Exception as e:
                    log.warning("preview_segment_extract_failed", path=vp, error=str(e)[:200])
                    audio_for_asr = await _extract_audio_safe(vp)

            tr, piece, weak = await _transcribe_social_audio(
                client,
                audio_for_asr,
                caption_hint=hint,
                session=session,
            )
            speech_weak_any = speech_weak_any or weak
            if piece:
                if len(video_paths) > 1:
                    chunks.append(f"[Видео {vi}]\n{piece}")
                else:
                    chunks.append(piece)
            audio_sec += tr.audio_seconds or 0.0
            remaining -= tr.audio_seconds or seg_dur

        text = "\n\n".join(chunks)
        if speech_weak_any and not text.strip():
            text = ""
        if extra_block:
            text = f"{text}\n\n{extra_block}".strip() if text else extra_block

        frames_dir = str(Path(primary_video).with_name(Path(primary_video).stem + "_preview_frames"))
        frames = await extract_keyframes(
            primary_video,
            frames_dir,
            every_seconds=max(8, int(preview_budget / 4)),
            max_frames=6,
            duration_limit=preview_budget,
        )
        ocr = await ocr_frames(frames)
        if ocr:
            text = f"{text}\n\n[On-screen text OCR preview]\n{ocr}" if text else f"[On-screen text OCR preview]\n{ocr}"
        return text, title, total_duration, audio_sec, primary_video

    chunks = []
    audio_sec = 0.0
    speech_weak_any = False
    for vi, vp in enumerate(video_paths, 1):
        audio_for_asr = await _extract_audio_safe(vp)
        tr, piece, weak = await _transcribe_social_audio(
            client,
            audio_for_asr,
            caption_hint=hint,
            session=session,
        )
        speech_weak_any = speech_weak_any or weak
        if piece:
            if len(video_paths) > 1:
                chunks.append(f"[Видео {vi}]\n{piece}")
            else:
                chunks.append(piece)
        audio_sec += tr.audio_seconds or 0.0

    text = "\n\n".join(chunks)
    if speech_weak_any and not text.strip():
        text = ""
    if extra_block:
        text = f"{text}\n\n{extra_block}".strip() if text else extra_block

    out_atlas = atlas_dir or str(Path(primary_video).with_name(Path(primary_video).stem + "_atlas"))
    try:
        atlas_frames = await build_screen_atlas(
            primary_video,
            out_atlas,
            duration=total_duration,
            source_type=source_type,
            keep_top_n=24,
        )
        ocr_block = format_timed_ocr_block(atlas_frames)
        if ocr_block:
            text = f"{text}\n\n{ocr_block}".strip() if text else ocr_block
        elif source_type == "video":
            frames_dir = str(Path(primary_video).with_name(Path(primary_video).stem + "_frames"))
            frames = await extract_keyframes(primary_video, frames_dir)
            ocr = await ocr_frames(frames)
            if ocr:
                text = f"{text}\n\n[On-screen text OCR]\n{ocr}".strip() if text else f"[On-screen text OCR]\n{ocr}"
    except Exception as e:
        log.warning("screen_atlas_failed", error=str(e)[:300])
        if source_type == "video":
            frames_dir = str(Path(primary_video).with_name(Path(primary_video).stem + "_frames"))
            frames = await extract_keyframes(primary_video, frames_dir)
            ocr = await ocr_frames(frames)
            if ocr:
                text = f"{text}\n\n[On-screen text OCR]\n{ocr}".strip() if text else f"[On-screen text OCR]\n{ocr}"
    return text, title, total_duration, audio_sec, primary_video


async def extract_text_for_material(
    *,
    source_type: str,
    source_url: str | None,
    file_path: str | None,
    note_text: str | None,
    stage: str,
    settings: Settings | None = None,
    atlas_dir: str | None = None,
    session=None,
    on_progress=None,
) -> tuple[str, str | None, float | None, float, str | None]:
    """
    Returns (raw_text, title, duration_seconds, audio_seconds_used, media_path).
    media_path — local video/media file worth persisting for screenshots.
    atlas_dir — optional dir for timed OCR atlas (materials/{id}/atlas).
    on_progress — optional async callback(step: str) for UI stages (fetch/asr/…).
    """
    settings = settings or get_settings()

    async def _progress(step: str) -> None:
        if on_progress is None:
            return
        await on_progress(step)

    if source_type == "note":
        await _progress("fetch")
        return (note_text or "").strip(), (note_text or "")[:80], None, 0.0, None

    if source_type == "article":
        assert source_url
        await _progress("fetch")
        title, text = await extract_article(source_url)
        return text, title, None, 0.0, None

    if source_type == "pdf":
        assert file_path
        await _progress("fetch")
        text = await extract_pdf_text(file_path)
        await _progress("asr")
        return text, Path(file_path).name, None, 0.0, file_path

    if source_type == "image":
        assert file_path
        await _progress("fetch")
        text = await extract_image_text(file_path)
        await _progress("asr")
        return text, Path(file_path).name, None, 0.0, file_path

    if source_type == "voice":
        assert file_path
        from app.services.audio_media import WHISPER_NATIVE_SUFFIXES

        await _progress("fetch")
        media_path = file_path
        audio_for_asr = file_path
        suffix = Path(file_path).suffix.lower()
        if suffix not in WHISPER_NATIVE_SUFFIXES:
            # Convert exotic containers (aac/wma/aiff/amr/…) via ffmpeg → mp3
            mp3 = str(Path(file_path).with_suffix(".mp3"))
            try:
                audio_for_asr = await extract_audio(file_path, mp3)
            except Exception as e:
                log.warning("voice_convert_failed", error=str(e)[:200], path=file_path)
                audio_for_asr = file_path

        await _progress("asr")
        client = await _whisper_client(settings, session=session)
        # Voice notes are almost always Russian
        result = await client.transcribe(audio_for_asr, language="ru")
        title = Path(file_path).stem or "Голосовая заметка"
        return (
            result.text,
            title,
            result.audio_seconds or None,
            result.audio_seconds,
            media_path,
        )

    if source_type in {"youtube", "instagram", "tiktok", "video"}:
        if source_type == "youtube" and source_url and getattr(settings, "youtube_prefer_captions", True):
            try:
                captions = await fetch_youtube_captions(source_url)
            except Exception as cap_err:
                log.warning("youtube_captions_failed", error=str(cap_err)[:200])
                captions = None
            if captions and captions.segments:
                from app.services.localize import localize_transcript_to_russian

                total_duration = captions.duration if captions.duration > 0 else None
                threshold_sec = settings.video_preview_threshold_min * 60
                is_long = (total_duration or 0.0) > threshold_sec
                do_preview = stage == "preview" and is_long and bool(total_duration)
                if do_preview and total_duration:
                    preview_budget = min(
                        float(total_duration),
                        max(30.0, float(total_duration) * settings.video_preview_fraction),
                    )
                    raw_cap = captions.format_text(max_seconds=preview_budget)
                    if len(raw_cap.strip()) < 40:
                        raw_cap = captions.format_text()
                else:
                    raw_cap = captions.format_text()

                raw_cap = normalize_asr_glitches(raw_cap)
                raw_cap = await localize_transcript_to_russian(
                    raw_cap, language=captions.language, session=session
                )
                if captions.description.strip():
                    raw_cap = f"{raw_cap}\n\n{captions.description.strip()}" if raw_cap else captions.description.strip()
                log.info(
                    "youtube_captions_used",
                    language=captions.language,
                    is_auto=captions.is_auto,
                    duration=total_duration,
                    stage=stage,
                    chars=len(raw_cap),
                )
                return raw_cap, captions.title, total_duration, 0.0, None

        if source_type in {"youtube", "instagram", "tiktok"}:
            assert source_url
            await _progress("fetch")
            try:
                bundle = await download_media_bundle(source_url, label=source_type)
            except Exception as download_err:
                if (
                    file_path
                    and Path(file_path).is_file()
                    and Path(file_path).stat().st_size > 0
                    and Path(file_path).suffix.lower() in _VIDEO_FILE_SUFFIXES
                ):
                    log.warning(
                        "media_download_failed_using_local",
                        source_type=source_type,
                        file_path=file_path,
                        error=str(download_err)[:200],
                    )
                    return await _extract_video_pipeline(
                        video_paths=[file_path],
                        title=None,
                        duration=None,
                        source_type=source_type,
                        stage=stage,
                        settings=settings,
                        atlas_dir=atlas_dir,
                        session=session,
                        on_progress=_progress,
                    )
                raise
            title = bundle.title
            duration = bundle.duration or None
            if bundle.image_paths:
                slide_root = Path(atlas_dir).parent / "slides" if atlas_dir else Path(bundle.image_paths[0]).parent / "slides"
                persisted = _persist_slides(bundle.image_paths, str(slide_root))
                if persisted:
                    bundle.image_paths = persisted
            extras: list[str] = []
            if bundle.caption.strip():
                extras.append(bundle.caption.strip())
            for i, img in enumerate(bundle.image_paths, 1):
                slide = await extract_image_text(img)
                extras.append(f"[Слайд {i}]\n{slide}")
            extra_block = "\n\n".join(extras)

            if not bundle.has_video:
                await _progress("asr")
                media = bundle.image_paths[0] if bundle.image_paths else None
                text = extra_block or title
                return text, title, None, 0.0, media

            return await _extract_video_pipeline(
                video_paths=bundle.video_paths,
                title=title,
                duration=duration,
                source_type=source_type,
                stage=stage,
                settings=settings,
                atlas_dir=atlas_dir,
                session=session,
                extra_block=extra_block,
                caption_hint=bundle.caption or "",
                on_progress=_progress,
            )

        assert file_path
        await _progress("fetch")
        return await _extract_video_pipeline(
            video_paths=[file_path],
            title=Path(file_path).name,
            duration=None,
            source_type=source_type,
            stage=stage,
            settings=settings,
            atlas_dir=atlas_dir,
            session=session,
            caption_hint=Path(file_path).name,
            on_progress=_progress,
        )

    raise ValueError(f"Unknown source_type={source_type}")
