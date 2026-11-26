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
from app.pipelines.youtube import download_media_bundle
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

    if is_weak_speech(text) and first_lang != "en":
        log.info("asr_retry_en", reason="weak_or_hallucination")
        tr2 = await client.transcribe(audio_path, language="en")
        text2 = normalize_asr_glitches(tr2.text)
        if not is_weak_speech(text2) or len(speech_body_text(text2)) > len(speech_body_text(text)):
            tr, text = tr2, text2
    elif is_weak_speech(text) and first_lang == "en":
        # Caption looked EN but still weak — one auto retry
        log.info("asr_retry_auto", reason="en_still_weak")
        tr2 = await client.transcribe(audio_path, language="auto")
        text2 = normalize_asr_glitches(tr2.text)
        if not is_weak_speech(text2) and len(speech_body_text(text2)) > len(speech_body_text(text)):
            tr, text = tr2, text2

    if is_weak_speech(text):
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


async def _whisper_client(settings: Settings):
    try:
        return get_transcribe_client(settings)
    except RuntimeError:
        from app.ai import get_transcribe_client_pooled
        from app.db import SessionLocal

        async with SessionLocal() as session:
            return await get_transcribe_client_pooled(session)


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
        client = await _whisper_client(settings)
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
        if source_type in {"youtube", "instagram", "tiktok"}:
            assert source_url
            await _progress("fetch")
            bundle = await download_media_bundle(source_url, label=source_type)
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

            await _progress("asr")
            client = await _whisper_client(settings)
            chunks: list[str] = []
            audio_sec = 0.0
            speech_weak_any = False
            for vi, vp in enumerate(bundle.video_paths, 1):
                audio_path = str(Path(vp).with_suffix(".mp3"))
                try:
                    audio_path = await extract_audio(vp, audio_path)
                except Exception:
                    audio_path = vp
                tr, piece, weak = await _transcribe_social_audio(
                    client,
                    audio_path,
                    caption_hint=bundle.caption or "",
                    session=session,
                )
                speech_weak_any = speech_weak_any or weak
                if piece:
                    if len(bundle.video_paths) > 1:
                        chunks.append(f"[Видео {vi}]\n{piece}")
                    else:
                        chunks.append(piece)
                audio_sec += tr.audio_seconds or 0
            text = "\n\n".join(chunks)
            if speech_weak_any and not text.strip():
                text = ""
            if extra_block:
                text = f"{text}\n\n{extra_block}".strip() if text else extra_block

            media_path = bundle.video_path
            video_for_frames = media_path
            out_atlas = atlas_dir
            if not out_atlas and video_for_frames:
                out_atlas = str(Path(video_for_frames).with_name(Path(video_for_frames).stem + "_atlas"))
            try:
                atlas_frames = await build_screen_atlas(
                    video_for_frames,
                    out_atlas,
                    duration=duration,
                    source_type=source_type,
                    keep_top_n=24,
                )
                ocr_block = format_timed_ocr_block(atlas_frames)
                if ocr_block:
                    text = f"{text}\n\n{ocr_block}"
            except Exception as e:
                log.warning("screen_atlas_failed", error=str(e)[:300])
            return text, title, duration, audio_sec, media_path

        assert file_path
        await _progress("fetch")
        title = Path(file_path).name
        duration = await probe_duration_seconds(file_path)
        audio_path = str(Path(file_path).with_suffix(".mp3"))
        audio_path = await extract_audio(file_path, audio_path)
        video_for_frames = file_path
        media_path = file_path

        threshold_sec = settings.video_preview_threshold_min * 60
        is_long = (duration or 0) > threshold_sec
        do_preview = stage == "preview" and is_long

        await _progress("asr")
        client = await _whisper_client(settings)
        if do_preview and duration:
            seg_dur = max(30.0, duration * settings.video_preview_fraction)
            seg_path = str(Path(audio_path).with_name(Path(audio_path).stem + "_preview.mp3"))
            src_for_cut = video_for_frames
            try:
                await extract_audio_segment(src_for_cut, 0, seg_dur, seg_path)
                tr, text, _weak = await _transcribe_social_audio(
                    client, seg_path, caption_hint=title, session=session
                )
            except Exception:
                tr, text, _weak = await _transcribe_social_audio(
                    client, audio_path, caption_hint=title, session=session
                )
            frames_dir = str(Path(audio_path).with_name(Path(audio_path).stem + "_preview_frames"))
            frames = await extract_keyframes(
                video_for_frames, frames_dir, every_seconds=max(8, int(seg_dur / 4)), max_frames=6
            )
            ocr = await ocr_frames(frames)
            if ocr:
                text = f"{text}\n\n[On-screen text OCR preview]\n{ocr}".strip() if text else f"[On-screen text OCR preview]\n{ocr}"
            return text, title, duration, tr.audio_seconds, media_path

        tr, text, _weak = await _transcribe_social_audio(
            client, audio_path, caption_hint=title, session=session
        )
        out_atlas = atlas_dir
        if not out_atlas and video_for_frames:
            out_atlas = str(Path(video_for_frames).with_name(Path(video_for_frames).stem + "_atlas"))
        try:
            atlas_frames = await build_screen_atlas(
                video_for_frames,
                out_atlas,
                duration=duration,
                source_type=source_type,
                keep_top_n=24,
            )
            ocr_block = format_timed_ocr_block(atlas_frames)
            if ocr_block:
                text = f"{text}\n\n{ocr_block}".strip() if text else ocr_block
            else:
                frames_dir = str(Path(audio_path).with_name(Path(audio_path).stem + "_frames"))
                frames = await extract_keyframes(video_for_frames, frames_dir)
                ocr = await ocr_frames(frames)
                if ocr:
                    text = f"{text}\n\n[On-screen text OCR]\n{ocr}".strip() if text else f"[On-screen text OCR]\n{ocr}"
        except Exception as e:
            log.warning("screen_atlas_failed", error=str(e)[:300])
            frames_dir = str(Path(audio_path).with_name(Path(audio_path).stem + "_frames"))
            frames = await extract_keyframes(video_for_frames, frames_dir)
            ocr = await ocr_frames(frames)
            if ocr:
                text = f"{text}\n\n[On-screen text OCR]\n{ocr}".strip() if text else f"[On-screen text OCR]\n{ocr}"
        return text, title, duration, tr.audio_seconds, media_path

    raise ValueError(f"Unknown source_type={source_type}")
