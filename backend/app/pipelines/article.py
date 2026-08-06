from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from readability import Document

from app.logging_setup import get_logger

log = get_logger(__name__)


async def extract_article(url: str) -> tuple[str, str]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        resp = await client.get(url, headers={"User-Agent": "ChronicumBot/0.1"})
        resp.raise_for_status()
        html = resp.text

    def _parse() -> tuple[str, str]:
        doc = Document(html)
        title = (doc.title() or "").strip() or url
        content_html = doc.summary()
        soup = BeautifulSoup(content_html, "lxml")
        text = soup.get_text("\n", strip=True)
        return title, text

    return await asyncio.to_thread(_parse)
