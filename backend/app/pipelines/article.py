from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document

from app.logging_setup import get_logger

log = get_logger(__name__)

MAX_ARTICLE_BYTES = 5 * 1024 * 1024  # 5 MiB cap for fetched HTML/text
MAX_REDIRECTS = 5
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata",
    }
)
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


class UnsafeURLError(ValueError):
    """Raised when a URL targets a non-HTTP scheme or private/internal network host."""


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NET:
        return False
    return bool(ip.is_global)


async def validate_public_http_url(url: str) -> str:
    """Validate that `url` is a public http/https URL and resolves only to globally routable IPs."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError(f"Unsupported URL scheme: {parsed.scheme or '(empty)'}")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL is missing a hostname")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        raise UnsafeURLError(f"Blocked internal host: {host}")

    # Direct IP literal check before DNS
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not _is_public_ip(host):
            raise UnsafeURLError(f"Blocked non-public IP address: {host}")
        return raw

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeURLError(f"Could not resolve host {host}: {exc}") from exc

    addrs = {info[4][0] for info in infos if info and info[4]}
    if not addrs:
        raise UnsafeURLError(f"No DNS addresses for host: {host}")
    for addr in addrs:
        if not _is_public_ip(addr):
            raise UnsafeURLError(f"Blocked non-public resolved address for {host}: {addr}")
    return raw


def _check_content_type(content_type: str) -> None:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if not ct:
        return
    if ct.startswith("text/") or ct in {"application/xhtml+xml", "application/xml", "application/json"}:
        return
    raise ValueError(f"Unsupported article Content-Type: {ct}")


async def extract_article(url: str, *, max_bytes: int = MAX_ARTICLE_BYTES) -> tuple[str, str]:
    current_url = await validate_public_http_url(url)
    headers = {"User-Agent": "ChronicumBot/0.1"}

    html = ""
    async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", current_url, headers=headers) as resp:
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("location")
                    if not location:
                        resp.raise_for_status()
                    next_url = urljoin(str(resp.url), location)
                    current_url = await validate_public_http_url(next_url)
                    continue

                resp.raise_for_status()
                _check_content_type(resp.headers.get("content-type", ""))
                cl_hdr = resp.headers.get("content-length")
                if cl_hdr:
                    try:
                        if int(cl_hdr) > max_bytes:
                            raise ValueError(f"Article response exceeds {max_bytes} bytes limit")
                    except ValueError as exc:
                        if "exceeds" in str(exc):
                            raise

                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise ValueError(f"Article response exceeds {max_bytes} bytes limit")
                encoding = resp.encoding or "utf-8"
                html = bytes(buf).decode(encoding, errors="replace")
                break
        else:
            raise ValueError(f"Too many redirects (>{MAX_REDIRECTS}) while fetching article")

    def _parse() -> tuple[str, str]:
        doc = Document(html)
        title = (doc.title() or "").strip() or current_url
        content_html = doc.summary()
        soup = BeautifulSoup(content_html, "lxml")
        text = soup.get_text("\n", strip=True)
        return title, text

    return await asyncio.to_thread(_parse)
