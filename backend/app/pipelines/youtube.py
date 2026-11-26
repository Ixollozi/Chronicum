from __future__ import annotations

import asyncio
from dataclasses import dataclass
import html
import json
from pathlib import Path
import re

from app.config import get_settings
from app.logging_setup import get_logger
from app.pipelines.media_bundle import MediaBundle
from app.services.media import extract_audio, probe_duration_seconds
from app.services.storage import ensure_storage

log = get_logger(__name__)

_SUBTITLE_NOISE_RE = re.compile(
    r"^\s*(?:[\[\(]\s*(?:music|applause|laughter|cheering|silence|sound|музыка|аплодисменты|смех)\s*[\]\)]|[♪♫\s]+)\s*$",
    re.IGNORECASE,
)
_VTT_TIME_RE = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_CAPTION_FORMAT_PRIORITY = ("json3", "vtt", "srt", "srv3", "ttml")


@dataclass
class YouTubeCaptions:
    title: str
    duration: float
    language: str
    is_auto: bool
    segments: list[tuple[float, str]]
    description: str = ""

    def format_text(self, max_seconds: float | None = None) -> str:
        lines: list[str] = []
        for start_sec, text in self.segments:
            if max_seconds is not None and start_sec > max_seconds:
                break
            sec_i = max(0, int(start_sec))
            hours, rem = divmod(sec_i, 3600)
            mins, secs = divmod(rem, 60)
            ts = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"
            lines.append(f"[{ts}] {text}")
        return "\n".join(lines)


def _clean_caption_text(raw: str) -> str:
    txt = html.unescape(_TAG_RE.sub("", raw or ""))
    txt = txt.replace("\u200b", "").replace("\ufeff", "")
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt or _SUBTITLE_NOISE_RE.match(txt):
        return ""
    return txt


def _dedup_rolling_segments(segments: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Collapse rolling / overlapping caption cues typical of YouTube auto-captions."""
    out: list[tuple[float, str]] = []
    prev_norm = ""
    for start_sec, text in segments:
        cleaned = _clean_caption_text(text)
        if not cleaned:
            continue
        norm = cleaned.lower()
        if norm == prev_norm:
            continue
        if prev_norm and norm.startswith(prev_norm) and len(norm) > len(prev_norm):
            boundary = norm[len(prev_norm)]
            if boundary in " \t.,!?;:-—":
                delta = cleaned[len(prev_norm) :].strip(" \t-—,:;")
                if not delta:
                    continue
                cleaned = delta
                norm = cleaned.lower()
        elif prev_norm and prev_norm.endswith(f" {norm}"):
            continue
        out.append((start_sec, cleaned))
        prev_norm = norm
    return out


def _parse_json3_captions(payload: str) -> list[tuple[float, str]]:
    data = json.loads(payload)
    raw_segments: list[tuple[float, str]] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        piece = "".join(str(s.get("utf8") or "") for s in segs).replace("\n", " ")
        cleaned = _clean_caption_text(piece)
        if not cleaned:
            continue
        start_ms = float(ev.get("tStartMs") or 0)
        raw_segments.append((start_ms / 1000.0, cleaned))
    return _dedup_rolling_segments(raw_segments)


def _parse_vtt_or_srt_captions(payload: str) -> list[tuple[float, str]]:
    raw_segments: list[tuple[float, str]] = []
    lines = payload.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    n = len(lines)
    while i < n:
        m = _VTT_TIME_RE.search(lines[i])
        if not m:
            i += 1
            continue
        h = int(m.group(1) or 0)
        mins = int(m.group(2))
        secs = int(m.group(3))
        ms_raw = m.group(4).ljust(3, "0")[:3]
        start_sec = h * 3600 + mins * 60 + secs + int(ms_raw) / 1000.0
        i += 1
        cue_lines: list[str] = []
        while i < n and lines[i].strip() != "":
            line = lines[i].strip()
            if _VTT_TIME_RE.search(line):
                break
            cleaned_line = _clean_caption_text(line)
            if cleaned_line and (not cue_lines or cue_lines[-1].lower() != cleaned_line.lower()):
                cue_lines.append(cleaned_line)
            i += 1
        if cue_lines:
            raw_segments.append((start_sec, " ".join(cue_lines)))
    return _dedup_rolling_segments(raw_segments)


def parse_subtitle_payload(payload: str, fmt: str = "vtt") -> list[tuple[float, str]]:
    """Parse YouTube json3 / WebVTT / SRT subtitle payload into (start_sec, text) tuples."""
    if not payload or not payload.strip():
        return []
    fmt_l = (fmt or "").strip().lower()
    if fmt_l == "json3" or payload.lstrip().startswith("{"):
        try:
            return _parse_json3_captions(payload)
        except Exception:
            if fmt_l == "json3":
                return []
    return _parse_vtt_or_srt_captions(payload)


def _match_track_key(tracks: dict, lang: str) -> str | None:
    if not tracks or not lang:
        return None
    lang_l = lang.lower()
    if lang in tracks:
        return lang
    for key in tracks:
        kl = key.lower()
        if kl == lang_l:
            return key
    for key in tracks:
        kl = key.lower()
        if kl.startswith(f"{lang_l}-") or kl.startswith(f"{lang_l}_"):
            return key
    return None


def _pick_track_format(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    by_ext: dict[str, dict] = {}
    for item in entries:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        ext = str(item.get("ext") or "").lower()
        by_ext.setdefault(ext, item)
    for ext in _CAPTION_FORMAT_PRIORITY:
        if ext in by_ext:
            return by_ext[ext]
    return next(iter(by_ext.values()), None)


def _candidate_caption_tracks(
    info: dict, languages: tuple[str, ...] = ("ru", "en")
) -> list[tuple[str, bool, dict]]:
    """
    Order candidate subtitle tracks: manual subtitles first, then automatic captions.
    Prefers requested languages (default: ru, en) and video's declared language.
    """
    orig_lang = str(info.get("language") or "").split("-")[0].strip().lower()
    lang_order: list[str] = []
    for lang in (*languages, orig_lang):
        if lang and lang.lower() not in lang_order:
            lang_order.append(lang.lower())

    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    candidates: list[tuple[str, bool, dict]] = []
    seen_urls: set[str] = set()

    for is_auto, bucket in ((False, manual), (True, auto)):
        if not isinstance(bucket, dict):
            continue
        for lang in lang_order:
            key = _match_track_key(bucket, lang)
            if not key:
                continue
            picked = _pick_track_format(bucket.get(key) or [])
            if not picked or picked["url"] in seen_urls:
                continue
            seen_urls.add(picked["url"])
            candidates.append((key.split("-")[0].lower(), is_auto, picked))
    return candidates


def _fetch_youtube_captions_sync(
    url: str,
    *,
    cookies: str = "",
    cookies_from_browser: str = "",
    languages: tuple[str, ...] = ("ru", "en"),
    min_chars: int = 80,
) -> YouTubeCaptions | None:
    import httpx

    opts: dict = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    }
    if cookies and Path(cookies).exists():
        opts["cookiefile"] = cookies
    elif cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    try:
        _path, info = _ytdlp_run(url, opts, download=False)
    except Exception as exc:
        log.warning("youtube_captions_probe_failed", url=url[:120], error=str(exc)[:200])
        return None
    if not info or info.get("_type") == "playlist":
        return None

    candidates = _candidate_caption_tracks(info, languages=languages)
    if not candidates:
        return None

    title = info.get("title") or info.get("fulltitle") or "YouTube"
    duration = float(info.get("duration") or 0)
    description = (info.get("description") or "")[:2000]
    headers = {"User-Agent": opts["http_headers"]["User-Agent"]}

    with httpx.Client(follow_redirects=True, timeout=20.0, headers=headers) as client:
        for lang, is_auto, track in candidates:
            try:
                resp = client.get(track["url"])
                resp.raise_for_status()
            except Exception as exc:
                log.warning(
                    "youtube_caption_download_failed",
                    lang=lang,
                    auto=is_auto,
                    error=str(exc)[:180],
                )
                continue
            segments = parse_subtitle_payload(resp.text, fmt=str(track.get("ext") or "vtt"))
            text_len = sum(len(t) for _, t in segments)
            if text_len >= min_chars:
                return YouTubeCaptions(
                    title=title,
                    duration=duration,
                    language=lang,
                    is_auto=is_auto,
                    segments=segments,
                    description=description,
                )
    return None


async def fetch_youtube_captions(
    url: str,
    *,
    languages: tuple[str, ...] = ("ru", "en"),
    min_chars: int = 80,
) -> YouTubeCaptions | None:
    """
    Fetch YouTube subtitles / auto-captions without downloading media.
    Returns None when no usable track is available so callers can fall back to Whisper.
    """
    settings = get_settings()
    from app.services.cookies import resolve_cookies_file

    cookies = resolve_cookies_file(settings)
    cookies_browser = getattr(settings, "media_cookies_from_browser", "") or ""
    return await asyncio.to_thread(
        _fetch_youtube_captions_sync,
        url,
        cookies=cookies,
        cookies_from_browser=cookies_browser,
        languages=languages,
        min_chars=min_chars,
    )


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
            filename = ydl.prepare_filename(info) if (info and download) else ""
            return filename, info

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
    from uuid import uuid4

    out_dir = ensure_storage() / label / uuid4().hex[:12]
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    from app.services.cookies import resolve_cookies_file

    cookies = resolve_cookies_file(settings)
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
                        "Загрузи cookies.txt в Настройках → Режимы → Cookies "
                        "(или укажи MEDIA_COOKIES_FILE / MEDIA_COOKIES_FROM_BROWSER=chrome), "
                        "либо загрузи файл вручную."
                    ) from e2
            if is_tt:
                try:
                    from app.pipelines.tiktok import download_tiktok_images

                    return download_tiktok_images(url, out_dir, cookies_file=cookies)
                except Exception as e2:
                    raise RuntimeError(
                        f"Не удалось скачать TikTok: {msg[:200]}. "
                        "Загрузи cookies.txt в Настройках → Режимы → Cookies "
                        "(или укажи MEDIA_COOKIES_FILE / MEDIA_COOKIES_FROM_BROWSER=chrome), "
                        "либо загрузи файл вручную. Карусель /photo/ yt-dlp не качает — берём слайды с страницы."
                    ) from e2
            if "login" in msg.lower() or "cookie" in msg.lower():
                raise RuntimeError(
                    "Не удалось скачать медиа (нужны cookies). "
                    "Загрузи cookies.txt в Настройках → Режимы → Cookies "
                    "(или укажи MEDIA_COOKIES_FILE в .env), либо загрузи файл вручную."
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
