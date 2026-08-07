"""Harvest tools / brands / URLs from transcript + timed OCR (free heuristics)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# Canonical name → match patterns (lowercase)
_BRAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Adobe Premiere Pro", re.compile(r"\b(premiere\s*pro|adobe\s*premiere|premiere)\b", re.I)),
    ("DaVinci Resolve", re.compile(r"\b(davinci(\s*resolve)?|да\s*винчи)\b", re.I)),
    ("Final Cut Pro", re.compile(r"\b(final\s*cut(\s*pro)?)\b", re.I)),
    ("CapCut", re.compile(r"\b(cap\s*cute?|кап\s*кат|капкат)\b", re.I)),
    ("Envato Elements", re.compile(r"\b(envato(\s*elements)?|envatoelements)\b", re.I)),
    ("Storyblocks", re.compile(r"\b(story\s*blocks|storyblocks)\b", re.I)),
    ("Pexels", re.compile(r"\bpexels\b", re.I)),
    ("Google Fonts", re.compile(r"\b(fonts\.google\.com|google\s*fonts)\b", re.I)),
    ("DaFont", re.compile(r"\bdafont(\.com)?\b", re.I)),
    ("Motion Array", re.compile(r"\b(motion\s*array|motionarray)\b", re.I)),
    ("Creative Market", re.compile(r"\b(creative\s*market|creativemarket)\b", re.I)),
    ("Unsplash", re.compile(r"\bunsplash\b", re.I)),
    ("Pixabay", re.compile(r"\bpixabay\b", re.I)),
    ("ChatGPT", re.compile(r"\b(chat\s*gpt|chatgpt|чат[- ]?жпти|чатджпти|gpt-?4o?)\b", re.I)),
    ("Claude", re.compile(r"\b(claude\s*(ai|3|sonnet|opus)?)\b", re.I)),
    ("Midjourney", re.compile(r"\bmidjourney\b", re.I)),
    ("Runway", re.compile(r"\brunway\b", re.I)),
    ("ElevenLabs", re.compile(r"\b(eleven\s*labs|11\s*labs)\b", re.I)),
    ("Figma", re.compile(r"\bfigma\b", re.I)),
    ("After Effects", re.compile(r"\bafter\s*effects\b", re.I)),
    ("Photoshop", re.compile(r"\b(photo\s*shop|фотошоп)\b", re.I)),
    ("Canva", re.compile(r"\bcanva\b", re.I)),
    ("Notion", re.compile(r"\bnotion\b", re.I)),
    ("Descript", re.compile(r"\bdescript\b", re.I)),
    ("Opus Clip", re.compile(r"\bopus\s*clip\b", re.I)),
    ("VEED", re.compile(r"\bveed\.?io\b", re.I)),
    ("Epidemic Sound", re.compile(r"\bepidemic\s*sound\b", re.I)),
    ("Artlist", re.compile(r"\bartlist\b", re.I)),
    ("Facebook", re.compile(r"\b(facebook|face\s*book)\b", re.I)),
    ("Slack", re.compile(r"\b(slack|слаг)\b", re.I)),
    ("GitHub", re.compile(r"\b(github|гитхаб)\b", re.I)),
]

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_WWW_RE = re.compile(r"\bwww\.[a-z0-9][-a-z0-9.]+\.[a-z]{2,}(?:/[^\s<>\"')\]]*)?", re.I)
_BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w/])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|io|dev|app|org|net|ru|ai|co|"
    r"gg|me|tv|so|to|pro|info|tech|site|online|shop|blog)(?:/[^\s<>\"')\]]*)?",
    re.I,
)
_DOMAIN_HINTS: dict[str, str] = {
    "pexels.com": "https://www.pexels.com",
    "storyblocks.com": "https://www.storyblocks.com",
    "elements.envato.com": "https://elements.envato.com",
    "envato.com": "https://elements.envato.com",
    "unsplash.com": "https://www.unsplash.com",
    "chat.openai.com": "https://chat.openai.com",
    "chatgpt.com": "https://chatgpt.com",
    "github.com": "https://github.com",
}

_SKIP_BARE_HOSTS = {
    "example.com",
    "localhost",
    "instagram.com",
    "www.instagram.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "t.me",
    "facebook.com",
    "www.facebook.com",
}

# ASR glitch → fix (applied on raw text)
_ASR_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"чат[- ]?жпти", re.I), "ChatGPT"),
    (re.compile(r"чатджпти", re.I), "ChatGPT"),
    (re.compile(r"\bбиролы\b", re.I), "B-roll"),
    (re.compile(r"\bбирол\w*\b", re.I), "B-roll"),
    (re.compile(r"какой сеть тебе", re.I), "какой look тебе"),
    (re.compile(r"мессенджер\s+слаг", re.I), "мессенджер Slack"),
    (re.compile(r"\bслаг\b", re.I), "Slack"),
    (re.compile(r"американский\s+вконтакте", re.I), "Facebook"),
    (re.compile(r"\bcap\s*cute\b", re.I), "CapCut"),
    (re.compile(r"\bкап\s*кат\b", re.I), "CapCut"),
    # Whisper sometimes invents mid-roll cliffhangers on music/noise
    (re.compile(r"(?m)^\[.*?\]\s*Продолжение следует\.\.\.\s*$"), ""),
    (re.compile(r"\bПродолжение следует\.\.\.", re.I), ""),
]

# Only when a video-editor / CapCut context is present (avoid touching normal Russian)
_EDITING_ASR_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bофферл[её]й\w*\b", re.I), "overlay"),
    (re.compile(r"\bоверл[её]й\w*\b", re.I), "overlay"),
    (re.compile(r"\bлеяра?\b", re.I), "layer"),
    (re.compile(r"\bлеяр\w*\b", re.I), "layer"),
    (re.compile(r"\bкейфрейм\w*\b", re.I), "keyframe"),
    (re.compile(r"\bопасность\b", re.I), "opacity"),
    (re.compile(r"\bаппликаци[юя]\b", re.I), "приложение"),
]


@dataclass
class HarvestedResources:
    tools: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    brands_by_t: list[tuple[float, str]] = field(default_factory=list)

    def prompt_block(self) -> str:
        if not self.tools and not self.links:
            return ""
        lines = ["SCREEN RESOURCES (must use in tools/links/digest when relevant):"]
        if self.tools:
            lines.append("tools/brands: " + ", ".join(self.tools))
        if self.links:
            urls = [str(x.get("url") or "") for x in self.links if x.get("url")]
            lines.append("links: " + ", ".join(urls))
        return "\n".join(lines)


def normalize_asr_glitches(text: str) -> str:
    out = text or ""
    for pat, repl in _ASR_FIXES:
        out = pat.sub(repl, out)
    blob = out.lower()
    if re.search(r"cap\s*cut|кап\s*кат|keyframe|кейфрейм|overlay|оверл|timeline|таймлайн", blob):
        for pat, repl in _EDITING_ASR_FIXES:
            out = pat.sub(repl, out)
    return re.sub(r"\n{3,}", "\n\n", out)


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        key = x.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(x.strip())
    return out


def normalize_url(url: str) -> str:
    u = (url or "").strip().rstrip(".,;:!?)】」'\"")
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if not re.match(r"^https?://", u, re.I):
        # Bare host/path only — must look domain-like
        if not re.match(r"^(?:www\.)?[a-z0-9][-a-z0-9.]*\.[a-z]{2,}(?:/.*)?$", u, re.I):
            return ""
        u = "https://" + u.lstrip("/")
    return u


def link_key(url: str) -> str:
    u = normalize_url(url)
    if not u:
        return ""
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower().removeprefix("www.")
        if not host or "." not in host:
            return ""
        if " " in host or not re.match(r"^[a-z0-9.-]+$", host):
            return ""
        path = (p.path or "").rstrip("/")
        return f"{host}{path}".lower()
    except Exception:
        return ""


def coerce_link_item(item: Any, *, default_source: str = "") -> dict[str, str] | None:
    if item is None:
        return None
    if isinstance(item, str):
        url = normalize_url(item)
        if not url or not link_key(url):
            return None
        return {"url": url, "title": "", "source": default_source or "llm"}
    if isinstance(item, dict):
        url = normalize_url(str(item.get("url") or item.get("href") or ""))
        if not url or not link_key(url):
            return None
        title = str(item.get("title") or item.get("label") or "").strip()[:200]
        source = str(item.get("source") or default_source or "").strip()[:40]
        return {"url": url, "title": title, "source": source}
    return None


def coerce_link_items(raw: Any, *, default_source: str = "") -> list[dict[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, (str, dict)):
        items = [raw]
    else:
        items = list(raw)
    out: list[dict[str, str]] = []
    for it in items:
        link = coerce_link_item(it, default_source=default_source)
        if link:
            out.append(link)
    return merge_link_items(out)


def merge_link_items(*groups: list[Any]) -> list[dict[str, str]]:
    """Union link groups by normalized URL; prefer non-empty title/source."""
    by_key: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for group in groups:
        for raw in group or []:
            link = coerce_link_item(raw)
            if not link:
                continue
            key = link_key(link["url"])
            if not key:
                continue
            if key not in by_key:
                by_key[key] = link
                order.append(key)
                continue
            cur = by_key[key]
            if not cur.get("title") and link.get("title"):
                cur["title"] = link["title"]
            if not cur.get("source") and link.get("source"):
                cur["source"] = link["source"]
            if link.get("source") == "llm" and link.get("title"):
                cur["title"] = link["title"]
    return [by_key[k] for k in order]


def _add_url(bucket: list[dict[str, str]], url: str, *, title: str = "", source: str) -> None:
    link = coerce_link_item({"url": url, "title": title, "source": source})
    if not link:
        return
    host = urlparse(link["url"]).netloc.lower().removeprefix("www.")
    path = urlparse(link["url"]).path or "/"
    if host in {h.removeprefix("www.") for h in _SKIP_BARE_HOSTS} and source != "llm":
        if path in {"", "/"}:
            return
    bucket.append(link)


def _extract_urls_from_text(text: str, *, source: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for m in _URL_RE.finditer(text or ""):
        _add_url(found, m.group(0), source=source)
    for m in _WWW_RE.finditer(text or ""):
        _add_url(found, m.group(0), source=source)
    for m in _BARE_DOMAIN_RE.finditer(text or ""):
        host = m.group(0).split("/")[0].lower().removeprefix("www.")
        if host in {h.removeprefix("www.") for h in _SKIP_BARE_HOSTS}:
            continue
        _add_url(found, m.group(0), source=source)
    return merge_link_items(found)


def harvest_resources(raw_text: str | None) -> HarvestedResources:
    text = raw_text or ""
    tools: list[str] = []
    links: list[dict[str, str]] = []
    brands_by_t: list[tuple[float, str]] = []

    for name, pat in _BRAND_PATTERNS:
        if pat.search(text):
            tools.append(name)

    ocr_idx = text.find("[On-screen")
    speech = text if ocr_idx < 0 else text[:ocr_idx]
    ocr_part = "" if ocr_idx < 0 else text[ocr_idx:]

    links.extend(_extract_urls_from_text(speech, source="speech"))
    if ocr_part:
        links.extend(_extract_urls_from_text(ocr_part, source="ocr"))

    blob = text.lower()
    brand_urls = [
        ("pexels", _DOMAIN_HINTS["pexels.com"], "Pexels"),
        ("storyblocks", _DOMAIN_HINTS["storyblocks.com"], "Storyblocks"),
        ("story blocks", _DOMAIN_HINTS["storyblocks.com"], "Storyblocks"),
        ("envato", _DOMAIN_HINTS["elements.envato.com"], "Envato Elements"),
        ("unsplash", _DOMAIN_HINTS["unsplash.com"], "Unsplash"),
        ("chatgpt", _DOMAIN_HINTS["chatgpt.com"], "ChatGPT"),
        ("chat gpt", _DOMAIN_HINTS["chatgpt.com"], "ChatGPT"),
        ("fonts.google", "https://fonts.google.com", "Google Fonts"),
        ("dafont", "https://www.dafont.com", "DaFont"),
        ("motionarray", "https://motionarray.com", "Motion Array"),
        ("motion array", "https://motionarray.com", "Motion Array"),
        ("creativemarket", "https://creativemarket.com", "Creative Market"),
        ("creative market", "https://creativemarket.com", "Creative Market"),
        ("github.com", _DOMAIN_HINTS["github.com"], "GitHub"),
    ]
    for needle, url, title in brand_urls:
        if needle in blob:
            _add_url(links, url, title=title, source="brand")

    from app.services.screen_atlas import parse_timed_ocr_block

    for fr in parse_timed_ocr_block(text):
        for name, pat in _BRAND_PATTERNS:
            if pat.search(fr.text):
                brands_by_t.append((fr.t_sec, name))
                if name not in tools:
                    tools.append(name)
        links.extend(_extract_urls_from_text(fr.text or "", source="ocr"))

    return HarvestedResources(
        tools=_uniq(tools),
        links=merge_link_items(links),
        brands_by_t=brands_by_t,
    )


def merge_card_resources(
    card_tools: list[str],
    card_links: list[Any],
    harvest: HarvestedResources,
) -> tuple[list[str], list[dict[str, str]]]:
    """Union LLM tools/links with harvested ones; harvest fills gaps."""
    tools = _uniq([*(card_tools or []), *harvest.tools])
    llm_links = coerce_link_items(card_links, default_source="llm")
    for li in llm_links:
        if not li.get("source"):
            li["source"] = "llm"
    links = merge_link_items(harvest.links, llm_links)
    return tools, links


def token_set(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", (s or "").lower())}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def digest_lines(digest: str) -> list[str]:
    lines: list[str] = []
    for raw in (digest or "").replace("\r", "").split("\n"):
        line = raw.strip().lstrip("•-*–—").strip()
        if line:
            lines.append(line)
    return lines
