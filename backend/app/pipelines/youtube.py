from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import get_settings
from app.logging_setup import get_logger
from app.services.media import extract_audio, probe_duration_seconds
from app.services.storage import ensure_storage

log = get_logger(__name__)


def _ffmpeg_available() -> bool:
    import shutil

    return bool(shutil.which("ffmpeg"))


def _ytdlp_download(url: str, out_dir: Path, *, cookies: str = "", cookies_from_browser: str = "") -> tuple[str, str, float]:
    import yt_dlp

    out_tmpl = str(out_dir / "%(id)s.%(ext)s")
    # Prefer progressive single file when ffmpeg missing (common on Windows).
    # With ffmpeg, merge best video+audio for higher quality.
    fmt = "bv*+ba/b" if _ffmpeg_available() else "b"
    opts: dict = {
        "outtmpl": out_tmpl,
        "format": fmt,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        },
    }
    if cookies and Path(cookies).exists():
        opts["cookiefile"] = cookies
    elif cookies_from_browser:
        # e.g. "chrome" or "firefox"
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        mp4 = str(Path(path).with_suffix(".mp4"))
        if Path(mp4).exists():
            path = mp4
        elif not Path(path).exists():
            vid = info.get("id") or ""
            candidates = sorted(
                out_dir.glob(f"{vid}.*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                path = str(candidates[0])
        title = info.get("title") or info.get("fulltitle") or "media"
        description = (info.get("description") or "").strip()
        if description and len(title) < 8:
            title = description.split("\n", 1)[0][:120]
        duration = float(info.get("duration") or 0)
        return path, title, duration


async def download_media_url(url: str, *, label: str = "media") -> tuple[str, str, float]:
    """Download video/audio via yt-dlp (+ Instagram public fallback). Returns (path, title, duration)."""
    out_dir = ensure_storage() / label
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    cookies = getattr(settings, "media_cookies_file", "") or ""
    cookies_browser = getattr(settings, "media_cookies_from_browser", "") or ""
    is_ig = "instagram.com" in url.lower() or "instagr.am" in url.lower()

    def _download() -> tuple[str, str, float]:
        try:
            return _ytdlp_download(
                url,
                out_dir,
                cookies=cookies,
                cookies_from_browser=cookies_browser,
            )
        except Exception as e:
            msg = str(e)
            log.warning("ytdlp_failed", label=label, error=msg[:300])
            if is_ig:
                from app.pipelines.instagram import download_instagram_public

                try:
                    log.info("instagram_public_fallback", url=url[:120])
                    return download_instagram_public(url, out_dir)
                except Exception as e2:
                    log.exception("instagram_fallback_failed", error=str(e2)[:300])
                    if "login" in msg.lower() or "cookie" in msg.lower() or "rate-limit" in msg.lower():
                        raise RuntimeError(
                            "Не удалось скачать Instagram: Meta просит login/cookies, "
                            "публичный fallback тоже не сработал. "
                            "Варианты: MEDIA_COOKIES_FILE=cookies.txt, "
                            "MEDIA_COOKIES_FROM_BROWSER=chrome, "
                            "или загрузи ролик файлом на /add."
                        ) from e2
                    raise RuntimeError(f"Не удалось скачать Instagram: {e2}") from e2
            if "login" in msg.lower() or "cookie" in msg.lower():
                raise RuntimeError(
                    "Не удалось скачать медиа (нужны cookies). "
                    "Укажи MEDIA_COOKIES_FILE в .env или загрузи файл вручную."
                ) from e
            raise RuntimeError(f"Не удалось скачать медиа: {msg[:300]}") from e

    return await asyncio.to_thread(_download)


async def prepare_url_audio(url: str, *, label: str = "media") -> tuple[str, str, float, str]:
    media_path, title, duration = await download_media_url(url, label=label)
    if not media_path.endswith(".mp3"):
        audio = str(Path(media_path).with_suffix(".mp3"))
        try:
            audio = await extract_audio(media_path, audio)
        except Exception:
            audio = media_path
            if duration <= 0:
                duration = await probe_duration_seconds(audio)
    else:
        audio = media_path
        if duration <= 0:
            duration = await probe_duration_seconds(audio)
    return audio, title, duration, media_path


async def download_youtube(url: str) -> tuple[str, str, float]:
    return await download_media_url(url, label="youtube")


async def prepare_youtube_audio(url: str) -> tuple[str, str, float, str]:
    return await prepare_url_audio(url, label="youtube")
