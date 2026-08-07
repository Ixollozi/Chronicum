from __future__ import annotations

import asyncio
from pathlib import Path

import pytesseract
from PIL import Image

from app.ai import get_transcribe_client
from app.config import Settings, get_settings
from app.logging_setup import get_logger
from app.pipelines.article import extract_article
from app.pipelines.image import extract_image_text
from app.pipelines.pdf import extract_pdf_text
from app.pipelines.youtube import prepare_url_audio
from app.services.media import (
    extract_audio,
    extract_audio_segment,
    extract_keyframes,
    probe_duration_seconds,
)

log = get_logger(__name__)


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
        parts = []
        for p in paths:
            try:
                text = pytesseract.image_to_string(Image.open(p)).strip()
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
) -> tuple[str, str | None, float | None, float, str | None]:
    """
    Returns (raw_text, title, duration_seconds, audio_seconds_used, media_path).
    media_path — local video/media file worth persisting for screenshots.
    """
    settings = settings or get_settings()

    if source_type == "note":
        return (note_text or "").strip(), (note_text or "")[:80], None, 0.0, None

    if source_type == "article":
        assert source_url
        title, text = await extract_article(source_url)
        return text, title, None, 0.0, None

    if source_type == "pdf":
        assert file_path
        text = await extract_pdf_text(file_path)
        return text, Path(file_path).name, None, 0.0, file_path

    if source_type == "image":
        assert file_path
        text = await extract_image_text(file_path)
        return text, Path(file_path).name, None, 0.0, file_path

    if source_type == "voice":
        assert file_path
        from app.services.audio_media import WHISPER_NATIVE_SUFFIXES

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

        client = await _whisper_client(settings)
        result = await client.transcribe(audio_for_asr, language="ru")
        title = Path(file_path).stem or "Голосовая заметка"
        return (
            result.text,
            title,
            result.audio_seconds or None,
            result.audio_seconds,
            media_path,
        )

    if source_type in {"youtube", "instagram", "video"}:
        if source_type in {"youtube", "instagram"}:
            assert source_url
            label = "instagram" if source_type == "instagram" else "youtube"
            audio_path, title, duration, media_path = await prepare_url_audio(source_url, label=label)
            video_for_frames = media_path
        else:
            assert file_path
            title = Path(file_path).name
            duration = await probe_duration_seconds(file_path)
            audio_path = str(Path(file_path).with_suffix(".mp3"))
            audio_path = await extract_audio(file_path, audio_path)
            video_for_frames = file_path
            media_path = file_path

        threshold_sec = settings.video_preview_threshold_min * 60
        is_long = (duration or 0) > threshold_sec
        do_preview = stage == "preview" and is_long

        client = await _whisper_client(settings)
        if do_preview and duration:
            seg_dur = max(30.0, duration * settings.video_preview_fraction)
            seg_path = str(Path(audio_path).with_name(Path(audio_path).stem + "_preview.mp3"))
            src_for_cut = video_for_frames if source_type == "video" else audio_path
            try:
                await extract_audio_segment(src_for_cut, 0, seg_dur, seg_path)
                tr = await client.transcribe(seg_path, language="ru")
            except Exception:
                tr = await client.transcribe(audio_path, language="ru")
            text = tr.text
            frames_dir = str(Path(audio_path).with_name(Path(audio_path).stem + "_preview_frames"))
            frames = await extract_keyframes(
                video_for_frames, frames_dir, every_seconds=max(8, int(seg_dur / 4)), max_frames=6
            )
            ocr = await ocr_frames(frames)
            if ocr:
                text = f"{text}\n\n[On-screen text OCR preview]\n{ocr}"
            return text, title, duration, tr.audio_seconds, media_path

        tr = await client.transcribe(audio_path, language="ru")
        text = tr.text
        frames_dir = str(Path(audio_path).with_name(Path(audio_path).stem + "_frames"))
        frames = await extract_keyframes(video_for_frames, frames_dir)
        ocr = await ocr_frames(frames)
        if ocr:
            text = f"{text}\n\n[On-screen text OCR]\n{ocr}"
        return text, title, duration, tr.audio_seconds, media_path

    raise ValueError(f"Unknown source_type={source_type}")
