"""Instagram download: yt-dlp first, then public HTML/GraphQL fallback (no login)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from app.logging_setup import get_logger
from app.pipelines.browser_cookies import cookie_header, netscape_cookies_for_host
from app.pipelines.media_bundle import MediaBundle

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


def collect_instagram_assets(media: dict) -> list[tuple[str, str]]:
    """Return (kind, url) where kind is video|image. Handles reels and carousels."""
    if not isinstance(media, dict):
        return []
    edges = ((media.get("edge_sidecar_to_children") or {}).get("edges")) or []
    if edges:
        out: list[tuple[str, str]] = []
        for edge in edges:
            node = (edge or {}).get("node") or {}
            if isinstance(node, dict):
                out.extend(_node_assets(node))
        return out
    carousel = media.get("carousel_media")
    if isinstance(carousel, list) and carousel:
        out = []
        for node in carousel:
            if isinstance(node, dict):
                out.extend(_node_assets(node))
        return out
    return _node_assets(media)


def _node_assets(node: dict) -> list[tuple[str, str]]:
    video = node.get("video_url")
    if not video:
        versions = node.get("video_versions") or []
        if versions and isinstance(versions[0], dict):
            video = versions[0].get("url")
    if video or node.get("is_video"):
        if video:
            return [("video", str(video))]
    display = node.get("display_url")
    if not display:
        cands = ((node.get("image_versions2") or {}).get("candidates")) or []
        if cands and isinstance(cands[0], dict):
            display = cands[0].get("url")
    if display:
        return [("image", str(display))]
    return []


def parse_instagram_html_assets(html: str) -> list[tuple[str, str]]:
    """Sidecar / carousel URLs embedded in page JSON (not just og:image)."""
    text = html or ""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(items: list[tuple[str, str]]) -> None:
        for kind, src in items:
            if not src or src in seen:
                continue
            seen.add(src)
            found.append((kind, src))

    start = 0
    while True:
        pos = text.find("edge_sidecar_to_children", start)
        if pos < 0:
            break
        brace = text.find("{", pos)
        if brace >= 0:
            try:
                blob, _ = json.JSONDecoder().raw_decode(text[brace:])
                add(collect_instagram_assets({"edge_sidecar_to_children": blob}))
            except json.JSONDecodeError:
                pass
        start = pos + 24
        if len(found) >= 2:
            return found

    start = 0
    while True:
        pos = text.find("carousel_media", start)
        if pos < 0:
            break
        bracket = text.find("[", pos)
        if bracket >= 0:
            try:
                blob, _ = json.JSONDecoder().raw_decode(text[bracket:])
                if isinstance(blob, list):
                    add(collect_instagram_assets({"carousel_media": blob}))
            except json.JSONDecodeError:
                pass
        start = pos + 14
        if len(found) >= 2:
            return found

    return found


def _apply_cookies(headers: dict[str, str], cookies_file: str) -> tuple[dict[str, str], dict[str, str]]:
    jar = netscape_cookies_for_host(cookies_file, "instagram.com") if cookies_file else {}
    header = cookie_header(cookies_file, "instagram.com") if cookies_file else ""
    if header:
        headers = {**headers, "Cookie": header}
    return headers, jar


def _download_url(client: httpx.Client, src: str, dest: Path, referer: str) -> None:
    if src.startswith("//"):
        src = "https:" + src
    with client.stream("GET", src, headers=_headers(referer)) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)


def download_instagram_public(
    url: str,
    dest_dir: Path,
    *,
    cookies_file: str = "",
) -> MediaBundle:
    """Download a public Reel/Post (video and/or photo carousel)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    shortcode = extract_shortcode(url) or "media"
    title = f"instagram:{shortcode}"
    duration = 0.0
    assets: list[tuple[str, str]] = []
    headers, jar = _apply_cookies(_headers(), cookies_file)

    with httpx.Client(follow_redirects=True, timeout=45.0, headers=headers, cookies=jar) as client:
        pages = [url]
        if shortcode != "media":
            pages.append(f"https://www.instagram.com/p/{shortcode}/embed/captioned/")
            pages.append(f"https://www.instagram.com/p/{shortcode}/embed/")
        html = ""
        for page_url in pages:
            try:
                page = client.get(page_url)
                html = page.text or ""
            except Exception as exc:
                log.warning("instagram_page_failed", url=page_url[:80], error=str(exc)[:160])
                continue
            og_title = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
                html,
                re.I,
            )
            if og_title:
                title = og_title.group(1).replace("&amp;", "&")[:120]
            parsed = parse_instagram_html_assets(html)
            if parsed:
                assets = parsed
                break

        if not assets and shortcode != "media":
            # Full Chrome headers get an empty JS app shell from Instagram;
            # a bare UA gets the lite embed with sidecar JSON + og tags.
            for page_url in (
                f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
                f"https://www.instagram.com/p/{shortcode}/embed/",
                url,
            ):
                try:
                    page = client.get(page_url, headers={"User-Agent": "Mozilla/5.0"})
                    lite = page.text or ""
                except Exception as exc:
                    log.warning("instagram_lite_page_failed", error=str(exc)[:160])
                    continue
                if lite:
                    html = lite
                parsed = parse_instagram_html_assets(lite)
                if parsed:
                    assets = parsed
                    break

        og_image = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.I,
        )
        og_video = re.search(
            r'<meta[^>]+property=["\']og:video["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.I,
        ) or re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video["\']',
            html,
            re.I,
        )

        if shortcode != "media":
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
                    title = _title_from(media, title)
                    duration = float(media.get("video_duration") or 0)
                    gql_assets = collect_instagram_assets(media)
                    if len(gql_assets) > len(assets):
                        assets = gql_assets
            except Exception as e:
                log.warning("instagram_gql_failed", error=str(e)[:200])

        if not assets:
            if og_video:
                assets = [("video", og_video.group(1).replace("&amp;", "&"))]
            elif og_image:
                assets = [("image", og_image.group(1).replace("&amp;", "&"))]

        if not assets:
            raise RuntimeError(
                "Публичная выгрузка Instagram не нашла видео/фото "
                "(приватный пост, карусель закрыта, rate-limit или смена API)."
            )

        videos: list[str] = []
        images: list[str] = []
        for i, (kind, src) in enumerate(assets, start=1):
            ext = ".mp4" if kind == "video" else ".jpg"
            dest = dest_dir / f"{shortcode}_{i}{ext}"
            try:
                _download_url(client, src, dest, url)
            except Exception as exc:
                log.warning("instagram_asset_failed", index=i, error=str(exc)[:160])
                continue
            if not dest.exists() or dest.stat().st_size < 400:
                continue
            if kind == "video":
                videos.append(str(dest))
            else:
                images.append(str(dest))

    if not videos and not images:
        raise RuntimeError("Скачанные Instagram-файлы пустые")

    return MediaBundle(title=title, duration=duration, video_paths=videos, image_paths=images)
