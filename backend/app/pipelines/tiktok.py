from __future__ import annotations

import json
import re
from pathlib import Path

from app.pipelines.browser_cookies import netscape_cookies_for_host
from app.pipelines.media_bundle import MediaBundle

_IMAGE_POST = re.compile(r'"imagePost"\s*:\s*\{')
_PHOTO_PATH = re.compile(r"/photo/(\d+)", re.I)
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def ytdlp_tiktok_url(url: str) -> str:
    """yt-dlp TikTokIE only matches /video/; /photo/ posts share the same aweme id."""
    return _PHOTO_PATH.sub(r"/video/\1", url or "")


def parse_tiktok_image_urls(html: str) -> list[str]:
    """Slideshow CDN URLs from a TikTok page (yt-dlp has no photo extractor)."""
    text = html or ""
    urls: list[str] = []
    for blob in _json_blobs(text):
        urls.extend(_urls_from_image_post_tree(blob))
        if urls:
            break
    if not urls:
        match = _IMAGE_POST.search(text)
        if match:
            start = text.find("{", match.start())
            if start >= 0:
                try:
                    blob, _ = json.JSONDecoder().raw_decode(text[start:])
                    urls.extend(_urls_from_image_post_tree({"imagePost": blob}))
                except json.JSONDecodeError:
                    pass
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        clean = str(url).replace("\\u002F", "/")
        if not clean.startswith("http") or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _json_blobs(html: str) -> list[object]:
    blobs: list[object] = []
    for marker in (
        "__UNIVERSAL_DATA_FOR_REHYDRATION__",
        "SIGI_STATE",
        "__NEXT_DATA__",
    ):
        blob = _script_json(html, marker)
        if blob is not None:
            blobs.append(blob)
    return blobs


def _script_json(html: str, script_id: str) -> object | None:
    token = f'id="{script_id}"'
    alt = f"id='{script_id}'"
    idx = html.find(token)
    if idx < 0:
        idx = html.find(alt)
    if idx < 0:
        return None
    start = html.find(">", idx)
    if start < 0:
        return None
    start += 1
    try:
        return json.JSONDecoder().raw_decode(html[start:].lstrip())[0]
    except json.JSONDecodeError:
        return None


def _urls_from_image_post_tree(obj) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            post = node.get("imagePost")
            if isinstance(post, dict):
                found.extend(_urls_from_image_post(post))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found


def _urls_from_image_post(post: dict) -> list[str]:
    urls: list[str] = []
    for img in post.get("images") or []:
        if not isinstance(img, dict):
            continue
        listing = []
        for key in ("imageURL", "displayImage", "ownerWatermarkImage", "thumbnail"):
            listing = ((img.get(key) or {}).get("urlList")) or []
            if listing:
                break
        if listing:
            urls.append(str(listing[-1]))
    return urls


def fetch_tiktok_html(url: str, *, cookies_file: str = "") -> str:
    """Browser-like GET; curl_cffi impersonation beats plain httpx against TikTok WAF."""
    headers = {"User-Agent": _CHROME_UA, "Referer": "https://www.tiktok.com/"}
    cookies = netscape_cookies_for_host(cookies_file, "tiktok.com") if cookies_file else {}
    try:
        from curl_cffi import requests as creq

        resp = creq.get(
            url,
            impersonate="chrome120",
            headers=headers,
            cookies=cookies or None,
            timeout=45,
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text or ""
    except Exception:
        import httpx

        with httpx.Client(
            follow_redirects=True,
            timeout=45.0,
            headers=headers,
            cookies=cookies,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text or ""


def download_tiktok_images(url: str, out_dir: Path, *, cookies_file: str = "") -> MediaBundle:

    out_dir.mkdir(parents=True, exist_ok=True)
    html = fetch_tiktok_html(url, cookies_file=cookies_file)
    urls = parse_tiktok_image_urls(html)
    if not urls:
        raise RuntimeError("TikTok photo: imagePost не найден")
    title = "tiktok photo"
    og = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if og:
        title = og.group(1)[:120]
    headers = {"User-Agent": _CHROME_UA, "Referer": "https://www.tiktok.com/"}
    paths: list[str] = []
    try:
        from curl_cffi import requests as creq

        for i, src in enumerate(urls, start=1):
            dest = out_dir / f"tt_photo_{i}.jpg"
            resp = creq.get(src, impersonate="chrome120", headers=headers, timeout=45)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            if dest.stat().st_size > 400:
                paths.append(str(dest))
    except Exception:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=45.0, headers=headers) as client:
            for i, src in enumerate(urls, start=1):
                dest = out_dir / f"tt_photo_{i}.jpg"
                resp = client.get(src)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                if dest.stat().st_size > 400:
                    paths.append(str(dest))
    if not paths:
        raise RuntimeError("TikTok photo: файлы пустые")
    return MediaBundle(title=title, image_paths=paths)
