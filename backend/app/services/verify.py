"""Online verification of card links (GitHub API / HEAD) with cache.

Offline or timeout degrades to verified=unknown — never blocks processing.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 24 * 3600


async def verify_url(url: str, *, timeout: float = 5.0) -> dict:
    now = time.time()
    hit = _CACHE.get(url)
    if hit and now - hit[0] < _TTL:
        return dict(hit[1])
    result = {"verified": "unknown", "http_status": None}
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower().removeprefix("www.")
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if host == "github.com":
                parts = [x for x in p.path.split("/") if x]
                if len(parts) >= 2:
                    r = await client.get(
                        f"https://api.github.com/repos/{parts[0]}/{parts[1]}",
                        headers={"Accept": "application/vnd.github+json", "User-Agent": "chronicum-verify"},
                    )
                    result = {
                        "verified": "yes" if r.status_code == 200 else ("no" if r.status_code == 404 else "unknown"),
                        "http_status": r.status_code,
                    }
                else:
                    r = await client.head(url)
                    result = {"verified": "yes" if r.status_code < 400 else "unknown", "http_status": r.status_code}
            else:
                r = await client.head(url)
                if r.status_code >= 400:
                    r = await client.get(url)
                result = {
                    "verified": "yes" if r.status_code < 400 else ("no" if r.status_code == 404 else "unknown"),
                    "http_status": r.status_code,
                }
    except Exception:
        result = {"verified": "unknown", "http_status": None}
    _CACHE[url] = (now, result)
    return dict(result)


async def verify_links(links: list[dict], *, concurrency: int = 6) -> list[dict]:
    import asyncio

    items = [li for li in (links or []) if str(li.get("url") or "").strip()]
    if not items:
        return []
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(li: dict) -> dict:
        async with sem:
            res = await verify_url(str(li.get("url") or ""))
            return {**li, **res}

    return list(await asyncio.gather(*(_one(li) for li in items)))


_REPO_CACHE: dict[str, tuple[float, dict | None]] = {}


def _compact(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def pick_repo_match(product: str, items: list[dict]) -> dict | None:
    """Pick a GitHub search result that plausibly IS the named product.

    Exact / near-exact repo name only. Substring+stars (brag-awesome) is rejected —
    empty link beats a wrong popular repo.
    """
    target = _compact(product)
    if len(target) < 3:
        return None
    best: tuple[int, int, dict] | None = None
    for it in items or []:
        full = str(it.get("full_name") or "")
        if "/" not in full:
            continue
        owner, repo = full.split("/", 1)
        rc = _compact(repo)
        if not rc:
            continue
        # Product name == repo OWNER means we searched a vendor, not a product:
        # only a repo literally named after it counts (NVIDIA/nvidia-docker ✗).
        if _compact(owner) == target and rc != target:
            continue
        if rc == target:
            score = 3
        elif rc.startswith(target) and len(rc) <= len(target) + 2:
            # near-exact suffix noise only (bragjs), not brag-awesome
            score = 2
        elif target.startswith(rc) and len(rc) >= len(target) - 1 and len(rc) >= 4:
            score = 2
        else:
            continue
        stars = int(it.get("stargazers_count") or 0)
        cand = (score, stars, it)
        if best is None or cand[:2] > best[:2]:
            best = cand
    # Require exact (3) for short names; near-exact OK for longer products
    if best is None:
        return None
    if len(target) <= 5 and best[0] < 3:
        return None
    return best[2]


_GENERIC_TOOL_WORDS = {
    "github", "google", "youtube", "video", "audio", "code", "text",
    "logo", "design", "skill", "skills", "agent", "chat", "app", "web",
    "api", "bot", "ai",
}


def repo_candidate_names(tools: list, title: str = "", raw_text: str = "") -> list[str]:
    """Order product names for repo resolution: title/speech first, then tools.

    OCR chrome tools are already filtered before this runs; still prefer names
    that appear in the title or spoken evidence.
    """
    import re

    from app.services.grounding import extract_entities, spoken_blob

    title_l = (title or "").lower()
    spoken = spoken_blob(raw_text or "")
    seeds: list[str] = []
    # Title tokens / entities (e.g. «Навык Brag: …»)
    for ent in extract_entities(title or ""):
        seeds.append(ent)
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9.+-]{2,})\b", title or ""):
        seeds.append(m.group(1))
    for ent in extract_entities(spoken[:4000]):
        seeds.append(ent)
    seeds.extend(tools or [])

    cands: list[tuple[int, int, str]] = []
    for t in seeds:
        name = (t or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.+-]{3,}", name):
            continue
        if name.lower() in _GENERIC_TOOL_WORDS:
            continue
        rank = 0
        if name.lower() in title_l:
            rank -= 10
        if name.lower() in spoken:
            rank -= 6
        if re.search(r"[a-z][A-Z]", name):
            rank -= 5
        cands.append((rank, -len(name), name))
    cands.sort()
    seen: list[str] = []
    for _, _, name in cands:
        if name not in seen:
            seen.append(name)
    return seen[:5]


async def resolve_github_repo(product: str, *, timeout: float = 6.0) -> dict | None:
    """Live-resolve a named product to its GitHub repo (replaces local lists).

    Returns a verified link dict or None. Cached 24h; offline → None.
    Weak / substring matches are rejected (empty beats wrong popular repo).
    """
    key = _compact(product)
    now = time.time()
    hit = _REPO_CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return dict(hit[1]) if hit[1] else None
    result: dict | None = None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": f"{product} in:name", "per_page": 5},
                headers={"Accept": "application/vnd.github+json", "User-Agent": "chronicum-resolve"},
            )
            if r.status_code == 200:
                match = pick_repo_match(product, (r.json() or {}).get("items") or [])
                if match:
                    full = str(match.get("full_name"))
                    result = {
                        "url": f"https://github.com/{full}",
                        "title": full,
                        "source": "web-resolve",
                        "verified": "yes",
                        "http_status": 200,
                    }
    except Exception:
        result = None
    _REPO_CACHE[key] = (now, result)
    return dict(result) if result else None


async def resolve_repo_from_candidates(
    candidates: list[str],
    *,
    evidence_text: str = "",
    timeout: float = 6.0,
) -> dict | None:
    """Try up to 3 product names; prefer literal owner/repo slug in evidence."""
    import re

    blob = evidence_text or ""
    for slug in github_slug_candidates(blob):
        owner, _, repo = slug.partition("/")
        # Prefer slug when any candidate matches the repo name
        for name in candidates or []:
            if _compact(name) and _compact(name) == _compact(repo):
                url = f"https://github.com/{slug}"
                try:
                    res = await verify_url(url, timeout=timeout)
                    if res.get("verified") == "no":
                        break
                    return {
                        "url": url,
                        "title": slug,
                        "source": "evidence-slug",
                        "verified": res.get("verified") or "yes",
                        "http_status": res.get("http_status") or 200,
                    }
                except Exception:
                    return {
                        "url": url,
                        "title": slug,
                        "source": "evidence-slug",
                        "verified": "unknown",
                    }
        # Exact slug in evidence without candidate match still wins once
        if re.search(rf"\b{re.escape(slug)}\b", blob, re.I):
            url = f"https://github.com/{slug}"
            return {
                "url": url,
                "title": slug,
                "source": "evidence-slug",
                "verified": "yes",
                "http_status": 200,
            }

    for name in (candidates or [])[:3]:
        found = await resolve_github_repo(name, timeout=timeout)
        if found:
            return found
    return None


def github_slug_candidates(text: str) -> list[str]:
    """owner/repo candidates mentioned anywhere (speech/OCR), for API check."""
    import re

    found: list[str] = []
    for m in re.finditer(r"\b([A-Za-z0-9][A-Za-z0-9-]{1,38})/([A-Za-z0-9_.-]{2,100})\b", text or ""):
        slug = f"{m.group(1)}/{m.group(2)}"
        low = slug.lower()
        if low.startswith(("http/", "www/", "api/")) or slug in found:
            continue
        found.append(slug)
    return found[:8]
