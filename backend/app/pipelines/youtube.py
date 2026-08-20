from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import get_settings
from app.logging_setup import get_logger
from app.pipelines.media_bundle import MediaBundle
from app.services.media import extract_audio, probe_duration_seconds
from app.services.storage import ensure_storage

log = get_logger(__name__)


def _ffmpeg_available() -> bool:
    import shutil

    return bool(shutil.which("ffmpeg"))


def _ytdlp_opts(
    out_dir: Path,
    *,
    cookies: str = "",
    cookies_from_browser: str = "",
    ignoreerrors: bool = False,
) -> dict:
    fmt = "bv*+ba/b" if _ffmpeg_available() else "b"
    opts: dict = {
        "outtmpl": str(out_dir / "%(id)s_%(autonumber)03d.%(ext)s"),
        "format": fmt,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    }
    if ignoreerrors:
        opts["ignoreerrors"] = True
    if cookies and Path(cookies).exists():
        opts["cookiefile"] = cookies
    elif cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        opts["impersonate"] = ImpersonateTarget(client="chrome")
    except Exception:
        pass
    return opts


def _ytdlp_run(url: str, opts: dict, *, download: bool = True):
    import yt_dlp

    def run(current: dict):
        with yt_dlp.YoutubeDL(current) as ydl:
            info = ydl.extract_info(url, download=download)
            return ydl.prepare_filename(info) if info else "", info

    try:
        return run(opts)
    except Exception as exc:
        msg = str(exc)
        tls_fail = any(token in msg for token in ("curl: (35)", "SSLError", "OPENSSL_internal", "TLS connect"))
        if not (opts.get("impersonate") and tls_fail):
            raise
        log.warning("ytdlp_impersonate_tls_failed", error=msg[:200])
        fallback = dict(opts)
        fallback.pop("impersonate", None)
        return run(fallback)


def _ytdlp_download(url: str, out_dir: Path, *, cookies: str = "", cookies_from_browser: str = "") -> tuple[str, str, float]:
    opts = _ytdlp_opts(out_dir, cookies=cookies, cookies_from_browser=cookies_from_browser)
    path, info = _ytdlp_run(url, opts)
    if not info:
        raise RuntimeError("yt-dlp не вернул метаданные")
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


def _ytdlp_bundle(
    url: str,
    out_dir: Path,
    *,
    cookies: str = "",
    cookies_from_browser: str = "",
) -> MediaBundle:
    opts = _ytdlp_opts(
        out_dir,
        cookies=cookies,
        cookies_from_browser=cookies_from_browser,
        ignoreerrors=True,
    )
    path, info = _ytdlp_run(url, opts)
    if not info:
        raise RuntimeError("yt-dlp не вернул метаданные")
    title = info.get("title") or info.get("fulltitle") or "media"
    caption = (info.get("description") or "")[:2000]
    duration = float(info.get("duration") or 0)
    if info.get("_type") == "playlist":
        videos: list[str] = []
        images: list[str] = []
        for i, entry in enumerate(info.get("entries") or [], start=1):
            if not entry:
                continue
            try:
                kind, dest = _materialize_entry(entry, out_dir, i)
            except Exception as exc:
                log.warning("carousel_slide_failed", index=i, error=str(exc)[:200])
                continue
            if kind == "video":
                videos.append(dest)
            else:
                images.append(dest)
        if not videos and not images:
            raise RuntimeError("Карусель скачалась пустой")
        return MediaBundle(
            title=title,
            duration=duration,
            video_paths=videos,
            image_paths=images,
            caption=caption,
        )
    mp4 = str(Path(path).with_suffix(".mp4"))
    if Path(mp4).exists():
        path = mp4
    elif not Path(path).exists():
        vid = info.get("id") or ""
        hits = sorted(out_dir.glob(f"{vid}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            path = str(hits[0])
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return MediaBundle(title=title, image_paths=[path], caption=caption)
    return MediaBundle(title=title, duration=duration, video_paths=[path], caption=caption)


def _materialize_entry(entry: dict, out_dir: Path, index: int) -> tuple[str, str]:
    import httpx

    formats = entry.get("formats") or []
    requested = entry.get("requested_downloads") or []
    candidates: list[str] = []
    for item in requested:
        fp = item.get("filepath") or item.get("filename")
        if fp:
            candidates.append(fp)
    vid = entry.get("id") or str(index)
    candidates.extend(str(p) for p in sorted(out_dir.glob(f"{vid}.*")))

    for fp in candidates:
        p = Path(fp)
        if p.exists() and p.stat().st_size > 400:
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                return "image", str(p)
            return "video", str(p)

    thumbs = entry.get("thumbnails") or []
    thumbs = sorted(thumbs, key=lambda t: t.get("width") or 0, reverse=True)
    url = None
    if thumbs:
        url = thumbs[0].get("url")
    if not url and not formats:
        url = entry.get("url")
    if not url:
        raise RuntimeError(f"Нет файла для слайда {index}")
    dest = out_dir / f"slide_{index}.jpg"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.instagram.com/"}
    with httpx.Client(follow_redirects=True, timeout=45.0, headers=headers) as client:
        r = client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return "image", str(dest)


def _merge_paths(bundle: MediaBundle, extra: MediaBundle) -> MediaBundle:
    seen_v = set(bundle.video_paths)
    for path in extra.video_paths:
        if path not in seen_v:
            bundle.video_paths.append(path)
            seen_v.add(path)
    seen_i = set(bundle.image_paths)
    for path in extra.image_paths:
        if path not in seen_i:
            bundle.image_paths.append(path)
            seen_i.add(path)
    if extra.caption and not bundle.caption:
        bundle.caption = extra.caption
    if extra.title and (not bundle.title or bundle.title in {"media", "tiktok photo"}):
        bundle.title = extra.title
    if extra.duration and not bundle.duration:
        bundle.duration = extra.duration
    return bundle


def _merge_tiktok_images(bundle: MediaBundle, url: str, out_dir: Path, cookies: str) -> MediaBundle:
    from app.pipelines.tiktok import download_tiktok_images

    try:
        photos = download_tiktok_images(url, out_dir, cookies_file=cookies)
    except Exception as exc:
        log.warning("tiktok_images_failed", error=str(exc)[:200])
        return bundle
    return _merge_paths(bundle, photos)


def _merge_instagram_carousel(bundle: MediaBundle, url: str, out_dir: Path, cookies: str) -> MediaBundle:
    from app.pipelines.instagram import download_instagram_public

    if len(bundle.image_paths) >= 2:
        return bundle
    try:
        extra = download_instagram_public(url, out_dir, cookies_file=cookies)
    except Exception as exc:
        log.warning("instagram_carousel_merge_failed", error=str(exc)[:200])
        return bundle
    return _merge_paths(bundle, extra)


async def download_media_bundle(url: str, *, label: str = "media") -> MediaBundle:
    out_dir = ensure_storage() / label
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    cookies = getattr(settings, "media_cookies_file", "") or ""
    cookies_browser = getattr(settings, "media_cookies_from_browser", "") or ""
    is_ig = "instagram.com" in url.lower() or "instagr.am" in url.lower()
    is_tt = "tiktok.com" in url.lower()
    ytdlp_url = url
    if is_tt:
        from app.pipelines.tiktok import ytdlp_tiktok_url

        ytdlp_url = ytdlp_tiktok_url(url)

    def _download() -> MediaBundle:
        try:
            bundle = _ytdlp_bundle(
                ytdlp_url,
                out_dir,
                cookies=cookies,
                cookies_from_browser=cookies_browser,
            )
            if is_ig:
                bundle = _merge_instagram_carousel(bundle, url, out_dir, cookies)
            if is_tt:
                bundle = _merge_tiktok_images(bundle, url, out_dir, cookies)
            return bundle
        except Exception as e:
            msg = str(e)
            log.warning("ytdlp_failed", label=label, error=msg[:300])
            if is_ig:
                from app.pipelines.instagram import download_instagram_public

                try:
                    log.info("instagram_public_fallback", url=url[:120])
                    return download_instagram_public(url, out_dir, cookies_file=cookies)
                except Exception as e2:
                    log.exception("instagram_fallback_failed", error=str(e2)[:300])
                    raise RuntimeError(
                        "Не удалось скачать Instagram (видео или карусель). "
                        "Варианты: MEDIA_COOKIES_FILE / MEDIA_COOKIES_FROM_BROWSER=chrome "
                        "или загрузи файлом."
                    ) from e2
            if is_tt:
                try:
                    from app.pipelines.tiktok import download_tiktok_images

                    return download_tiktok_images(url, out_dir, cookies_file=cookies)
                except Exception as e2:
                    raise RuntimeError(
                        f"Не удалось скачать TikTok: {msg[:200]}. "
                        "Нужны cookies (MEDIA_COOKIES_FILE / MEDIA_COOKIES_FROM_BROWSER=chrome) "
                        "или загрузи файл вручную. Карусель /photo/ yt-dlp не качает — берём слайды с страницы."
                    ) from e2
            if "login" in msg.lower() or "cookie" in msg.lower():
                raise RuntimeError(
                    "Не удалось скачать медиа (нужны cookies). "
                    "Укажи MEDIA_COOKIES_FILE в .env или загрузи файл вручную."
                ) from e
            raise RuntimeError(f"Не удалось скачать медиа: {msg[:300]}") from e

    return await asyncio.to_thread(_download)


async def download_media_url(url: str, *, label: str = "media") -> tuple[str, str, float]:
    """Video-first wrapper (moments / callers that need one media file)."""
    bundle = await download_media_bundle(url, label=label)
    path = bundle.video_path or (bundle.image_paths[0] if bundle.image_paths else None)
    if not path:
        raise RuntimeError("Медиа не скачалось")
    return path, bundle.title, bundle.duration


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
