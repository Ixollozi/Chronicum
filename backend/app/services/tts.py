"""Neural TTS via Microsoft Edge voices (natural book-like Russian)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

VOICES = {
    "svetlana": {
        "id": "ru-RU-SvetlanaNeural",
        "label": "Светлана",
    },
    "dmitry": {
        "id": "ru-RU-DmitryNeural",
        "label": "Дмитрий",
    },
}

DEFAULT_VOICE = "svetlana"
MAX_CHARS = 7000


def prepare_for_speech(text: str) -> str:
    """Clean markup but keep punctuation so neural prosody stays natural."""
    t = text.replace("\r\n", "\n")
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*]\([^)]*\)", " ", t)
    t = re.sub(r"\[(\d{1,2}:\d{2}(?::\d{2})?)]", " ", t)
    t = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"^>\s?", "", t, flags=re.M)
    t = re.sub(r"^[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"^\d+\.\s+", "", t, flags=re.M)
    t = re.sub(r"[*_~|#]+", " ", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\n+", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()[:MAX_CHARS]


def resolve_voice(key: str | None) -> tuple[str, str]:
    k = (key or DEFAULT_VOICE).strip().lower()
    if k not in VOICES:
        k = DEFAULT_VOICE
    meta = VOICES[k]
    return k, meta["id"]


def _cache_dir() -> Path:
    settings = get_settings()
    root = Path(settings.storage_path)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    d = root / "tts_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path_for(prepared: str, voice_id: str) -> Path:
    digest = hashlib.sha256(f"{voice_id}\n{prepared}".encode("utf-8")).hexdigest()[:40]
    return _cache_dir() / f"{digest}.mp3"


async def synthesize_speech(text: str, voice_key: str | None = None) -> tuple[Path, str]:
    """Return (mp3_path, voice_key). Fast path: disk cache."""
    prepared = prepare_for_speech(text)
    if not prepared:
        raise ValueError("Пустой текст для озвучки")

    key, voice_id = resolve_voice(voice_key)
    out = cache_path_for(prepared, voice_id)
    if out.is_file() and out.stat().st_size > 0:
        return out, key

    import edge_tts

    communicate = edge_tts.Communicate(prepared, voice_id)
    tmp = out.with_suffix(".part.mp3")
    try:
        await communicate.save(str(tmp))
        tmp.replace(out)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise

    log.info("tts_synthesized", voice=voice_id, chars=len(prepared), bytes=out.stat().st_size)
    return out, key


async def iter_speech_audio(text: str, voice_key: str | None = None) -> AsyncIterator[bytes]:
    """
    Yield mp3 bytes ASAP. Uses cache when present; otherwise streams from Edge
    and writes cache after the full stream.
    """
    prepared = prepare_for_speech(text)
    if not prepared:
        raise ValueError("Пустой текст для озвучки")

    key, voice_id = resolve_voice(voice_key)
    out = cache_path_for(prepared, voice_id)
    if out.is_file() and out.stat().st_size > 0:
        # Serve from cache in small frames for quick TTFB to clients that stream
        data = out.read_bytes()
        step = 16_384
        for i in range(0, len(data), step):
            yield data[i : i + step]
        return

    import edge_tts

    communicate = edge_tts.Communicate(prepared, voice_id)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            data = chunk["data"]
            if data:
                buf.extend(data)
                yield data

    if not buf:
        raise RuntimeError("TTS вернул пустой аудиопоток")

    tmp = out.with_suffix(".part.mp3")
    try:
        tmp.write_bytes(buf)
        tmp.replace(out)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    log.info("tts_synthesized_stream", voice=voice_id, chars=len(prepared), bytes=len(buf), voice_key=key)


def list_voices_public() -> list[dict[str, str]]:
    return [{"key": k, "label": v["label"]} for k, v in VOICES.items()]
