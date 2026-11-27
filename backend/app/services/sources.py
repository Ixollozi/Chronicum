"""URL source detection."""

from __future__ import annotations

import re
from urllib.parse import urlparse

INSTAGRAM = re.compile(r"(instagram\.com|instagr\.am)", re.I)
YOUTUBE = re.compile(r"(youtube\.com|youtu\.be)", re.I)
TIKTOK = re.compile(r"(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)", re.I)
UNSUPPORTED = re.compile(r"(facebook\.com/reel|fb\.watch)", re.I)


class UnsupportedSourceError(ValueError):
    pass


def classify_url(url: str) -> str:
    u = url.strip()
    if INSTAGRAM.search(u):
        return "instagram"
    if YOUTUBE.search(u):
        return "youtube"
    if TIKTOK.search(u):
        return "tiktok"
    if UNSUPPORTED.search(u):
        raise UnsupportedSourceError(
            "Этот источник по ссылке пока не поддерживается. "
            "Скачай видео и загрузи файлом на /add или перешли в Telegram."
        )
    path = (urlparse(u).path or "").lower()
    if path.endswith(".pdf"):
        return "pdf"
    return "article"
