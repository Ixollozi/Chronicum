"""Resolve and classify playable audio for materials."""

from __future__ import annotations

from pathlib import Path

from app.models import Material

AUDIO_SUFFIXES = {
    ".mp3",
    ".ogg",
    ".oga",
    ".opus",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".wma",
    ".aiff",
    ".aif",
    ".caf",
    ".amr",
    ".mpga",
    ".mpeg",
    ".mp2",
    ".weba",
}

# Whisper / Groq typically accept these without conversion
WHISPER_NATIVE_SUFFIXES = {
    ".flac",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".m4a",
    ".ogg",
    ".oga",
    ".wav",
    ".webm",
}


def is_audio_upload(suffix: str, mime: str) -> bool:
    s = (suffix or "").lower()
    m = (mime or "").lower()
    if s in AUDIO_SUFFIXES:
        return True
    if m.startswith("audio/"):
        return True
    # audio-only container sometimes reported as video/webm by browsers
    if s == ".webm" and ("audio" in m or m in {"", "application/octet-stream"}):
        # ambiguous: prefer audio only when kind forced or mime says audio
        return m.startswith("audio/")
    return False


def resolve_material_audio(material: Material) -> Path | None:
    """Best available audio path for playback (full preferred over preview)."""
    candidates: list[Path] = []
    fp = material.file_path
    if fp:
        p = Path(fp)
        if p.suffix.lower() in AUDIO_SUFFIXES or p.suffix.lower() == ".webm":
            candidates.append(p)
        stem = p.with_suffix("")
        candidates.append(p.with_suffix(".mp3"))
        candidates.append(Path(str(stem) + "_preview.mp3"))

    for c in candidates:
        try:
            if c.is_file() and c.stat().st_size > 0:
                return c
        except OSError:
            continue
    return None


def material_has_audio(material: Material) -> bool:
    return resolve_material_audio(material) is not None


def audio_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".mpeg": "audio/mpeg",
        ".mpga": "audio/mpeg",
        ".mp2": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".webm": "audio/webm",
        ".weba": "audio/webm",
        ".flac": "audio/flac",
        ".wma": "audio/x-ms-wma",
        ".aiff": "audio/aiff",
        ".aif": "audio/aiff",
        ".caf": "audio/x-caf",
        ".amr": "audio/amr",
    }.get(ext, "application/octet-stream")
