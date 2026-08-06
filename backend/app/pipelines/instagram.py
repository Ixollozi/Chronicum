"""Instagram download: yt-dlp first, then public HTML/GraphQL fallback (no login)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.logging_setup import get_logger

log = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_SHORTCODE = re.compile(
    r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)",
    re.I,
)
# Common Relay persisted-query id for shortcode media (yt-dlp / community)
_DOC_ID = "8845758582119845"


def extract_shortcode(url: str) -> str | None:
    m = _SHORTCODE.search(url)
    return m.group(1) if m else None


def _headers(referer: str | None = None) -> dict[str, str]:
    h = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        h["Referer"] = referer
    return h


def _find_video_url(obj) -> str | None:
    if isinstance(obj, dict):
        for key in ("video_url", "contentUrl", "src"):
            v = obj.get(key)
            if isinstance(v, str) and ".mp4" in v:
                return v
        for v in obj.values():
            found = _find_video_url(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_video_url(item)
            if found:
                return found
    return None


def _title_from(obj, fallback: str) -> str:
    if isinstance(obj, dict):
        for key in ("title", "accessibility_caption", "edge_media_to_caption"):
            if key == "edge_media_to_caption":
                try:
                    edges = obj["edge_media_to_caption"]["edges"]
                    text = edges[0]["node"]["text"]
                    if text:
                        return text.split("\n", 1)[0][:120]
                except (KeyError, IndexError, TypeError):
                    pass
            else:
                v = obj.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:120]
    return fallback


def download_instagram_public(url: str, dest_dir: Path) -> tuple[str, str, float]:
    """
    Download a public Reel/Post video without account cookies.
    Returns (local_path, title, duration_seconds).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    shortcode = extract_shortcode(url) or "media"
    video_url: str | None = None
    title = f"instagram:{shortcode}"
    duration = 0.0
    meta_blob: dict | list | None = None

    with httpx.Client(follow_redirects=True, timeout=45.0, headers=_headers()) as client:
        # 1) HTML page — og:video / embedded JSON
        page = client.get(url)
        html = page.text or ""

        og = re.search(
            r'<meta[^>]+property=["\']og:video["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.I,
        ) or re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video["\']',
            html,
            re.I,
        )
        if og:
            video_url = og.group(1).replace("&amp;", "&")

        og_title = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.I,
        )
        if og_title:
            title = og_title.group(1).replace("&amp;", "&")[:120]

        if not video_url:
            for pattern in (
                r'window\._sharedData\s*=\s*(\{.+?\});</script>',
                r'<script type="application/ld\+json">(\{.*?\})</script>',
                r'"video_url"\s*:\s*"([^"]+)"',
            ):
                m = re.search(pattern, html, re.S)
                if not m:
                    continue
                if pattern.endswith('"([^"]+)"'):
                    video_url = m.group(1).encode().decode("unicode_escape")
                    break
                try:
                    meta_blob = json.loads(m.group(1))
                    video_url = _find_video_url(meta_blob)
                    title = _title_from(meta_blob, title)
                    if video_url:
                        break
                except json.JSONDecodeError:
                    continue

        # 2) GraphQL persisted query (works for many public shortcodes)
        if not video_url and shortcode != "media":
            gql = client.post(
                "https://www.instagram.com/graphql/query",
                data={
                    "variables": json.dumps({"shortcode": shortcode}),
                    "doc_id": _DOC_ID,
                },
                headers={
                    **_headers(url),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-FB-Friendly-Name": "PolarisPostActionLoadPostQueryQuery",
                    "X-IG-App-ID": "936619743392459",
                },
            )
            try:
                data = gql.json()
                media = (
                    (data.get("data") or {}).get("xdt_shortcode_media")
                    or (data.get("data") or {}).get("shortcode_media")
                )
                if media:
                    meta_blob = media
                    video_url = media.get("video_url") or _find_video_url(media)
                    title = _title_from(media, title)
                    duration = float(media.get("video_duration") or 0)
            except Exception as e:
                log.warning("instagram_gql_failed", error=str(e)[:200])

        if not video_url:
            raise RuntimeError(
                "Публичная выгрузка Instagram не нашла video_url "
                "(приватный ролик, rate-limit или смена API)."
            )

        # Resolve CDN
        if video_url.startswith("//"):
            video_url = "https:" + video_url

        out = dest_dir / f"{shortcode}.mp4"
        with client.stream("GET", video_url, headers=_headers(url)) as resp:
            resp.raise_for_status()
            with out.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)

    if not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError("Скачанный Instagram-файл пустой или слишком маленький")

    return str(out), title, duration
