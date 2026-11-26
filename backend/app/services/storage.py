import hashlib
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from app.config import get_settings


def ensure_storage() -> Path:
    root = Path(get_settings().storage_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_bytes(data: bytes, suffix: str) -> str:
    root = ensure_storage()
    name = f"{uuid4().hex}{suffix}"
    path = root / name
    path.write_bytes(data)
    return str(path)


def save_upload(src_path: str, suffix: str | None = None) -> str:
    root = ensure_storage()
    ext = suffix or Path(src_path).suffix or ""
    dest = root / f"{uuid4().hex}{ext}"
    shutil.copy2(src_path, dest)
    return str(dest)


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def url_sha256(url: str) -> str:
    return hashlib.sha256(_canonical_url(url).encode("utf-8")).hexdigest()


_YOUTUBE_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?.*?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})",
    re.I,
)
_TIKTOK_POST = re.compile(r"tiktok\.com/@[^/]+/(?:video|photo)/(\d+)", re.I)
_IG_SHORTCODE = re.compile(
    r"(?:instagram\.com|instagr\.am)/(?:reel|p|tv)/([A-Za-z0-9_-]+)",
    re.I,
)


def _canonical_url(url: str) -> str:
    raw = (url or "").strip()
    match = _YOUTUBE_ID.search(raw)
    if match:
        return f"youtube:{match.group(1)}"
    match = _TIKTOK_POST.search(raw)
    if match:
        return f"tiktok:{match.group(1)}"
    match = _IG_SHORTCODE.search(raw)
    if match:
        return f"instagram:{match.group(1)}"
    # Drop tracking query/fragment so near-identical pastes collide
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(raw)
        path = (parts.path or "/").rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))
    except Exception:
        return raw.rstrip("/")
