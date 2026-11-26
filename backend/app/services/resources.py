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
    ("Claude", re.compile(r"\b(claude\s*(ai|3|sonnet|opus|fabel)?|claude\s*fabel)\b", re.I)),
    ("Gemini Omni", re.compile(r"\bgemini\s*omni\b", re.I)),
    ("Gemini", re.compile(r"\bgemini\b", re.I)),
    ("Seedance", re.compile(r"\bseedance(\s*\d+(?:\.\d+)?)?\b", re.I)),
    ("OmniFlash", re.compile(r"\b(omni\s*flash|omniflash)\b", re.I)),
    ("Google Flow", re.compile(r"\b(google\s*flow|labs\.google)\b", re.I)),
    ("Cloth", re.compile(r"\bcloth(\s*cowork|\s*ai)?\b", re.I)),
    ("Midjourney", re.compile(r"\bmidjourney\b", re.I)),
    ("Runway", re.compile(r"\brunway\b", re.I)),
    ("ElevenLabs", re.compile(r"\b(eleven\s*labs|11\s*labs)\b", re.I)),
    ("Figma", re.compile(r"\bfigma\b", re.I)),
    ("After Effects", re.compile(r"\bafter\s*effects\b", re.I)),
    ("Photoshop", re.compile(r"\b(photo\s*shop|фотошоп)\b", re.I)),
    ("Canva", re.compile(r"\bcanva\b", re.I)),
    ("Notion", re.compile(r"\bnotion\b", re.I)),
    ("Pencil", re.compile(r"\bpencil(\.app)?\b", re.I)),
    ("Descript", re.compile(r"\bdescript\b", re.I)),
    ("Opus Clip", re.compile(r"\bopus\s*clip\b", re.I)),
    ("VEED", re.compile(r"\bveed\.?io\b", re.I)),
    ("Epidemic Sound", re.compile(r"\bepidemic\s*sound\b", re.I)),
    ("Artlist", re.compile(r"\bartlist\b", re.I)),
    ("Facebook", re.compile(r"\b(facebook|face\s*book)\b", re.I)),
    ("Slack", re.compile(r"\b(slack|слаг)\b", re.I)),
    ("GitHub", re.compile(r"\b(github|гитхаб)\b", re.I)),
    ("NVIDIA", re.compile(r"\bnvidia\b", re.I)),
    ("SkillSpector", re.compile(r"\bskillspector\b", re.I)),
    ("Brag", re.compile(r"\bbrag\b", re.I)),
    ("Cursor", re.compile(r"\bcursor\b", re.I)),
    ("Codex", re.compile(r"\bcodex\b", re.I)),
    ("Kwork", re.compile(r"\b(kwork|кворк)\b", re.I)),
    ("TenChat", re.compile(r"\b(ten\s?chat|тенчат)\b", re.I)),
    ("Three.js", re.compile(r"\bthree\.?js\b", re.I)),
    ("p5.js", re.compile(r"\bp5\.?js\b", re.I)),
]

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_WWW_RE = re.compile(r"\bwww\.[a-z0-9][-a-z0-9.]+\.[a-z]{2,}(?:/[^\s<>\"')\]]*)?", re.I)
_BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w/])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|io|dev|app|org|net|ru|ai|co|"
    r"gg|me|tv|so|to|pro|info|tech|site|online|shop|blog)(?:/[^\s<>\"')\]]*)?",
    re.I,
)
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
    (re.compile(r"\bRybit\b", re.I), "Rybbit"),
    (re.compile(r"\bRybett?\b", re.I), "Rybbit"),
    (re.compile(r"\bVitter\b", re.I), "witr"),
    (re.compile(r"\bWcrybbit\b", re.I), "rybbit"),
    # Whisper: «в код-коде» → «в Cursor» (bare «код-коде» only in editing context below)
    (re.compile(r"\b((?:в|на|через|открыв\w*|скилл\w*|навык\w*)\s+)код[- ]?код[еа]?\b", re.I), r"\1Cursor"),
    # Whisper: «/brag» → «slash брак»
    (re.compile(r"\bslash\s+брак\b", re.I), "slash Brag"),
    (re.compile(r"\bнавык\s+брак\b", re.I), "навык Brag"),
    # Whisper invents outros / cliffhangers on music / talking-head noise
    (re.compile(r"(?m)^\[.*?\]\s*Продолжение следует\.\.\.\s*$"), ""),
    (re.compile(r"\bПродолжение следует\.\.\.", re.I), ""),
    (re.compile(r"(?m)^\[.*?\]\s*Спасибо за просмотр[!?.]*\s*$", re.I), ""),
    (re.compile(r"\bСпасибо за просмотр[!?.]*", re.I), ""),
    (re.compile(r"(?m)^\[.*?\]\s*Thanks for watching[!?.]*\s*$", re.I), ""),
    (re.compile(r"\bThanks for watching[!?.]*", re.I), ""),
    (re.compile(r"(?m)^\[.*?\]\s*Подписывайтесь\s+на\s+(наш\s+)?канал[!?.]*\s*$", re.I), ""),
]

_ASR_HALLUCINATION = re.compile(
    r"("
    r"спасибо\s+за\s+просмотр|"
    r"thanks\s+for\s+watching|"
    r"продолжение\s+следует|"
    r"подписывайтесь\s+на\s+(наш\s+)?канал|"
    r"like\s+and\s+subscribe|"
    r"please\s+subscribe"
    r")",
    re.I,
)
_TS_SPEECH_LINE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:\.(\d+))?\]\s*(.*)$")

_OCR_JUNK_HOSTS = {
    "wcrybbit.io",
    "wcrybbit.com",
    "tryoutier.com",
    "outlier-app.tryoutier.com",
    # XML namespaces in screenshots are not resources
    "w3.org",
    "www.w3.org",
    "ww.w3.org",
}

# Bare apex hosts with no product path — OCR noise, not useful archive links
_GENERIC_APEX_HOSTS = {
    "google.com",
    "google.ru",
    "bing.com",
    "yahoo.com",
    "apple.com",
    "microsoft.com",
    "amazon.com",
}

_OVERLAY_CTA = re.compile(
    r"\b("
    r"comment|subscribe|follow|like|share|подпис|коммент|пиши|лайк|"
    r"crushing|right\s+now|original\s+audio|more\s+posts"
    r")\b",
    re.I,
)
_OVERLAY_STOP = {
    "this",
    "that",
    "with",
    "from",
    "your",
    "free",
    "video",
    "design",
    "motion",
    "google",  # alone — need product context (Flow / Omni)
    "instagram",
    "youtube",
    "telegram",
    "comment",
    "claude",  # covered by brand patterns; avoid "Claude Fabel Is Crushing…"
}
_AD_JINGLE = re.compile(
    r"("
    r"\boreo\b|level(?:ing)?\s+up\s+your|"
    r"попробуй\s+улучшить\s+свою\s+игру|"
    r"повышаешь\s+уровень|"
    r"поверни,?\s*щ[её]лкни|"
    r"try\s+leveling\s+up"
    r")",
    re.I,
)
# Title Case / CamelCase / Name 2.0 / Name.zip product overlays on screen
_OVERLAY_PRODUCT = re.compile(
    r"\b("
    r"[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*){1,3}"
    r"(?:\s+\d+(?:\.\d+)?)?"
    r"|"
    r"[A-Z][a-zA-Z0-9]+\s+\d+(?:\.\d+)?"
    r"|"
    r"[A-Z][a-z]+(?:[A-Z][a-zA-Z0-9]+)+(?:\.zip)?"  # SkillSpector / SkillSpector.zip
    r")\b"
)

_GITHUB_SLUG_RE = re.compile(
    r"\b([A-Za-z0-9][\w.-]{0,38})/([A-Za-z0-9._-]{1,100})\b"
)
_GITHUB_SLUG_REJECT_OWNERS = {
    "http",
    "https",
    "www",
    "для",
    "step",
    "шаг",
    "com",
    "org",
    "net",
    # Common words that OCR/speech joins into fake slugs («and / or»)
    "and",
    "or",
    "the",
    "a",
    "an",
    "to",
    "in",
    "on",
    "for",
    "with",
    "of",
    "at",
    "by",
    "is",
    "it",
    "as",
    "if",
    "vs",
    "per",
    "via",
    # Common nouns / site sections are not owner/repo names («stats/quotes»)
    "stats",
    "quotes",
    "quote",
    "examples",
    "example",
    "docs",
    "blog",
    "news",
    "support",
    "about",
    "home",
    "search",
    "login",
    "signup",
    "settings",
    "profile",
    "issues",
    "pulls",
    "releases",
    "actions",
    "projects",
    "features",
    "pricing",
    "topics",
    "trending",
    "explore",
}

# NOTE: no local brand→repo list by design. Named-only products are resolved
# live via GitHub Search (verify.resolve_github_repo) at card-apply time.

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
    editing_ctx = bool(
        re.search(
            r"cap\s*cut|кап\s*кат|keyframe|кейфрейм|overlay|оверл|timeline|таймлайн|"
            r"\bcursor\b|скилл|навык|ide\b|editor|код[- ]?код",
            blob,
        )
    )
    if editing_ctx:
        # Bare «код-коде» → Cursor only in editor/skill transcripts
        out = re.sub(r"\bкод[- ]?код[еа]?\b", "Cursor", out, flags=re.I)
        for pat, repl in _EDITING_ASR_FIXES:
            out = pat.sub(repl, out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def salvage_product_names(raw_text: str | None, *, title: str = "") -> list[str]:
    """Recover product names Whisper mangled using title/OCR overlays.

    No brand→URL lists: only strings already present as TitleCase/CamelCase in
    title or content OCR, matched to a close token in speech.
    """
    from app.services.grounding import lev, spoken_blob

    text = raw_text or ""
    spoken = spoken_blob(text)
    spoken_toks = set(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", spoken))
    seeds: list[str] = []
    # Prefer multi-word title products («SEO Monster», «SkillSpector tip»)
    for m in re.finditer(
        r"\b([A-Z][A-Za-z0-9.+-]{1,}(?:\s+[A-Z][A-Za-z0-9.+-]{1,}){1,3})\b",
        title or "",
    ):
        seeds.append(m.group(1))
    multi = set(seeds)
    for m in re.finditer(r"\b([A-Z][a-zA-Z0-9.+-]{3,})\b", title or ""):
        # Skip ALL-CAPS stubs (SEO, MCP) unless CamelCase/long
        tok = m.group(1)
        if tok.isupper() and len(tok) <= 4:
            continue
        # Skip tokens already covered by a multi-word seed («Monster» in «SEO Monster»)
        if any(tok.lower() in s.lower().split() and " " in s for s in multi):
            continue
        seeds.append(tok)
    ocr_idx = text.find("[On-screen")
    ocr_part = text[ocr_idx:] if ocr_idx >= 0 else ""
    for ov in extract_overlay_product_names(ocr_part):
        if ov.isupper() and " " in ov:
            continue  # YOUR ASSISTANT etc.
        seeds.append(ov)
    out: list[str] = []
    for name in _uniq(seeds):
        low = name.lower()
        if re.match(r"^(?:run|open|install|comment|follow)\s+\S+$", low):
            continue
        if low in spoken:
            out.append(name)
            continue
        # Multi-word title products are trusted when any significant token is spoken
        parts = [p.lower() for p in name.split() if len(p) >= 4]
        if parts and any(p in spoken for p in parts):
            out.append(name)
            continue
        for st in spoken_toks:
            if abs(len(st) - len(low)) <= 1 and lev(st, low) <= 1 and len(low) >= 4:
                out.append(name)
                break
    return out


def speech_body_text(text: str) -> str:
    """Speech without timed prefixes / OCR blocks."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[On-screen") or s.startswith("[OCR ") or s.startswith("[Слайд"):
            break
        m = _TS_SPEECH_LINE.match(s)
        lines.append((m.group(4) if m else s).strip())
    return " ".join(x for x in lines if x)


def is_asr_hallucination(text: str) -> bool:
    body = speech_body_text(text)
    if not body:
        return True
    # Whole transcript is essentially a known outro hallucination
    if _ASR_HALLUCINATION.search(body) and len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", body)) <= 8:
        return True
    return False


def is_weak_speech(text: str, *, min_chars: int = 40) -> bool:
    """True when Whisper produced nothing useful (empty, hallucination, too short)."""
    body = speech_body_text(text)
    if not body or is_asr_hallucination(text):
        return True
    letters = re.findall(r"[A-Za-zА-Яа-яЁё0-9]", body)
    return len(letters) < min_chars


def caption_suggests_english(caption: str) -> bool:
    """Latin-heavy caption/hashtags → prefer EN Whisper first."""
    blob = (caption or "").strip()
    if not blob:
        return False
    # Strip URL noise
    blob = re.sub(r"https?://\S+", " ", blob)
    lat = len(re.findall(r"[A-Za-z]", blob))
    cyr = len(re.findall(r"[А-Яа-яЁё]", blob))
    if lat < 12:
        return False
    return lat >= cyr * 2 and (lat / max(1, lat + cyr)) >= 0.55


def strip_weak_speech_from_raw(raw: str) -> tuple[str, bool]:
    """
    If speech portion is weak/hallucinated, drop it and keep caption/OCR.
    Returns (cleaned_raw, speech_was_weak).
    """
    text = raw or ""
    ocr_idx = text.find("[On-screen")
    slide_idx = text.find("[Слайд")
    cut = len(text)
    for i in (ocr_idx, slide_idx):
        if i >= 0:
            cut = min(cut, i)
    speech = text[:cut].strip()
    rest = text[cut:].strip()
    if is_weak_speech(speech):
        return (rest if rest else ""), True
    return text, False


def extract_overlay_product_names(text: str) -> list[str]:
    """Heuristic Title-Case / versioned product names from OCR overlays."""
    # Drop timed OCR prefixes so "OCR 0:12" is not treated as a product
    cleaned = re.sub(r"\[OCR\s+\d+:\d+(?:\.\d+)?\]\s*", " ", text or "")
    cleaned = re.sub(r"\[On-screen[^\]]*\]", " ", cleaned)
    out: list[str] = []
    for m in _OVERLAY_PRODUCT.finditer(cleaned):
        name = re.sub(r"\s+", " ", m.group(0)).strip()
        if not name or len(name) < 4:
            continue
        if re.match(r"^OCR\b", name, re.I):
            continue
        if _OVERLAY_CTA.search(name):
            continue
        toks = name.split()
        # Single token OK when CamelCase product or *.zip
        camel = bool(re.search(r"[a-z][A-Z]", name))
        zip_name = bool(re.search(r"\.zip\b", name, re.I))
        if len(toks) < 2 and not re.search(r"\d", name) and not camel and not zip_name:
            continue
        if all(t.lower() in _OVERLAY_STOP for t in toks if not t[0].isdigit()):
            continue
        # Screen fragments, not products: single-letter tokens («D DTF VC»)
        # or trailing integer prices/counts («Pro Max 49», «Sive 80»)
        if any(len(t) == 1 and t.isalpha() for t in toks):
            continue
        if len(toks) >= 2 and re.fullmatch(r"\d+", toks[-1]):
            continue
        # Drop long marketing headlines (5+ words already capped by regex to 4)
        if len(toks) >= 4 and not re.search(r"\d", name):
            # Keep "Claude Fabel Is Crushing" out — prefer 2–3 token products
            continue
        if is_garble_tool(name):
            continue
        if zip_name:
            name = re.sub(r"\.zip\b", "", name, flags=re.I).strip()
        # Prefer known brand polish when pattern matches
        known_brand = False
        for brand, pat in _BRAND_PATTERNS:
            if pat.search(name):
                name = brand
                known_brand = True
                break
        # Single CamelCase overlays must be known brands — drop OCR soup (HocHoro).
        # Unknown CamelCase still survives via speech/LLM grounding downstream.
        if camel and len(toks) == 1 and not known_brand:
            continue
        out.append(name)
    return _uniq(out)


def screen_and_caption_blob(raw: str) -> str:
    """OCR / slide block + any non-speech leftovers for offtopic checks."""
    text = raw or ""
    ocr_idx = text.find("[On-screen")
    slide_idx = text.find("[Слайд")
    cut = -1
    for i in (ocr_idx, slide_idx):
        if i >= 0:
            cut = i if cut < 0 else min(cut, i)
    if cut < 0:
        return ""
    return text[cut:]


def is_speech_offtopic(raw: str, *, screen_tools: list[str] | None = None) -> bool:
    """
    Real speech that does not match on-screen/caption topic (ad jingle, music bed).
    Weak/empty speech is handled separately via is_weak_speech.
    """
    if is_weak_speech(raw):
        return False
    speech = speech_body_text(raw)
    screen = screen_and_caption_blob(raw)
    tools = list(screen_tools or [])
    if not tools:
        for name, pat in _BRAND_PATTERNS:
            if pat.search(screen):
                tools.append(name)
        tools.extend(extract_overlay_product_names(screen))
        tools = _uniq(tools)

    speech_l = speech.lower()
    screen_brands = [t for t in tools if t.lower() not in {"google", "telegram", "instagram"}]
    brand_in_speech = sum(
        1 for t in screen_brands if t.lower() in speech_l or t.lower().split()[0] in speech_l
    )

    if _AD_JINGLE.search(speech) and screen_brands and brand_in_speech == 0:
        return True

    speech_toks = token_set(speech)
    screen_toks = token_set(screen)
    for t in screen_brands:
        screen_toks |= token_set(t)

    if len(screen_brands) >= 2 and brand_in_speech == 0:
        if len(screen_toks) >= 4 and jaccard(speech_toks, screen_toks) < 0.12:
            return True

    if screen and len(token_set(screen)) >= 6:
        if jaccard(speech_toks, token_set(screen)) < 0.06 and brand_in_speech == 0 and screen_brands:
            return True
    return False


def speech_unreliable(raw: str, *, screen_tools: list[str] | None = None) -> bool:
    """Weak ASR or offtopic spoken audio — do not ground digest/moments in speech."""
    _, weak = strip_weak_speech_from_raw(raw or "")
    if weak:
        return True
    return is_speech_offtopic(raw or "", screen_tools=screen_tools)


def normalize_brand_names(text: str) -> str:
    """Same ASR brand fixes for card title/tools/digest after LLM."""
    return normalize_asr_glitches(text or "")


def _looks_like_github_slug(owner: str, repo: str) -> bool:
    o = (owner or "").strip()
    r = (repo or "").strip()
    if not o or not r:
        return False
    if o.lower() in _GITHUB_SLUG_REJECT_OWNERS or r.lower() in _GITHUB_SLUG_REJECT_OWNERS:
        return False
    # GitHub usernames cannot contain dots or the word «github»
    if "." in o or "github" in o.lower():
        return False
    if o.isdigit() or r.isdigit():
        return False
    if re.fullmatch(r"\d{1,2}[-./]\d{1,2}([./]\d{2,4})?", o) or re.fullmatch(
        r"\d{1,2}[-./]\d{1,2}([./]\d{2,4})?", r
    ):
        return False
    if len(o) < 2 or len(r) < 2:
        return False
    # Cyrillic path segments are not GitHub slugs
    if re.search(r"[а-яА-ЯёЁ]", o + r):
        return False
    return True


def extract_github_slugs(text: str, *, source: str = "ocr") -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for m in _GITHUB_SLUG_RE.finditer(text or ""):
        owner, repo = m.group(1), m.group(2)
        if not _looks_like_github_slug(owner, repo):
            continue
        # Skip if this is already part of a full github.com URL match region
        start = max(0, m.start() - 20)
        prefix = (text or "")[start : m.start()].lower()
        if "github.com/" in prefix.replace(" ", ""):
            continue
        url = f"https://github.com/{owner}/{repo}"
        _add_url(found, url, title=f"{owner}/{repo}", source=source)
    return merge_link_items(found)


def _cta_zone_sec(duration: float | None) -> float | None:
    """Start of end-CTA zone (last ~12% of video, min 6s tail)."""
    if not duration or duration <= 0:
        return None
    tail = max(6.0, duration * 0.12)
    return max(0.0, duration - tail)


def _is_cta_text(text: str) -> bool:
    low = (text or "").lower()
    return bool(
        re.search(
            r"\b(тг|telegram|t\.me|подпис|подписыва|наш\s+канал|github\s*radar)\b",
            low,
        )
    )


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
        out: dict[str, str] = {"url": url, "title": title, "source": source}
        if item.get("verified"):
            out["verified"] = str(item["verified"])[:20]
        if item.get("http_status") is not None:
            out["http_status"] = str(item["http_status"])[:10]
        return out
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
    """Union link groups by normalized URL; prefer non-empty title/source; drop OCR typo hosts."""
    by_key: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for group in groups:
        for raw in group or []:
            link = coerce_link_item(raw)
            if not link:
                continue
            try:
                host = urlparse(link["url"]).netloc.lower().removeprefix("www.")
            except Exception:
                host = ""
            if host in _OCR_JUNK_HOSTS:
                continue
            existing_hosts = [
                urlparse(by_key[k]["url"]).netloc.lower().removeprefix("www.") for k in order
            ]
            # Skip flatter OCR typos; replace a kept typo when a better host arrives
            replaced = False
            if host:
                for ek in list(order):
                    eh = urlparse(by_key[ek]["url"]).netloc.lower().removeprefix("www.")
                    if _hosts_near_typo(host, eh) and _host_more_structured(host, eh):
                        del by_key[ek]
                        order = [x for x in order if x != ek]
                        replaced = True
                        break
                if not replaced and _is_near_duplicate_host(host, existing_hosts):
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
            # Verification results survive re-merges (moments refresh etc.)
            if link.get("verified") and (
                not cur.get("verified") or link["verified"] == "yes"
            ):
                cur["verified"] = link["verified"]
                if link.get("http_status") is not None:
                    cur["http_status"] = link["http_status"]
    return [by_key[k] for k in order]


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


def _host_alnum(host: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (host or "").lower())


def _hosts_near_typo(a: str, b: str) -> bool:
    """Same TLD + ≤2 edit distance on full host or alnum-compact form."""
    ha, hb = (a or "").lower(), (b or "").lower()
    if not ha or not hb or ha == hb:
        return False
    if ha.rsplit(".", 1)[-1] != hb.rsplit(".", 1)[-1]:
        return False
    if _edit_distance(ha, hb) <= 2 and abs(len(ha) - len(hb)) <= 2:
        return True
    aa, bb = _host_alnum(ha), _host_alnum(hb)
    return bool(aa and bb and _edit_distance(aa, bb) <= 2 and abs(len(aa) - len(bb)) <= 2)


def _host_more_structured(a: str, b: str) -> bool:
    """Prefer seomonster.avansaber.com over seomontberavansaber.com."""
    return (a or "").count(".") > (b or "").count(".")


def _is_near_duplicate_host(host: str, existing: list[str]) -> bool:
    """True if host looks like a 1–2 char OCR typo of an already kept hostname."""
    h = (host or "").lower()
    if not h or "." not in h:
        return False
    for other in existing:
        if _hosts_near_typo(h, other):
            # New host is flatter typo of a better one already kept
            if not _host_more_structured(h, other):
                return True
    return False


def _add_url(bucket: list[dict[str, str]], url: str, *, title: str = "", source: str) -> None:
    link = coerce_link_item({"url": url, "title": title, "source": source})
    if not link:
        return
    host = urlparse(link["url"]).netloc.lower().removeprefix("www.")
    path = urlparse(link["url"]).path or "/"
    if host in _OCR_JUNK_HOSTS and source != "llm":
        return
    if host in {h.removeprefix("www.") for h in _SKIP_BARE_HOSTS} and source != "llm":
        if path in {"", "/"}:
            return
    # Bare google.com / similar apex without path — OCR noise, not archive-worthy
    if host in _GENERIC_APEX_HOSTS and path in {"", "/"} and source != "llm":
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


def harvest_resources(
    raw_text: str | None,
    *,
    duration_seconds: float | None = None,
    title: str = "",
) -> HarvestedResources:
    text = normalize_asr_glitches(raw_text or "")
    # OCR address bars often split URLs with spaces around slashes —
    # join ONLY on lines that actually mention github (else «and / or» prose breaks)
    text = re.sub(r"(github\.com)\s*/\s*", r"\1/", text, flags=re.I)
    _lines = []
    for _ln in text.splitlines():
        if "github" in _ln.lower():
            _ln = re.sub(r"\b([A-Za-z0-9][\w.-]{1,38})\s+/\s+([A-Za-z0-9._-]{2,100})\b", r"\1/\2", _ln)
        _lines.append(_ln)
    text = "\n".join(_lines)
    tools: list[str] = []
    links: list[dict[str, str]] = []
    brands_by_t: list[tuple[float, str]] = []
    cta_start = _cta_zone_sec(duration_seconds)

    ocr_idx = text.find("[On-screen")
    ocr_part = "" if ocr_idx < 0 else text[ocr_idx:]
    # Speech tools: strip timed OCR and chrome [Слайд]+[OCR] dumps first
    from app.services.grounding import is_chrome_ocr
    from app.services.screen_atlas import parse_timed_ocr_block, strip_ocr_blocks

    speech = strip_ocr_blocks(text)

    # Tools only from clean speech/caption (not slide/OCR chrome)
    for name, pat in _BRAND_PATTERNS:
        if pat.search(speech):
            tools.append(name)
    if re.search(r"\bRybbit\b", speech, re.I):
        tools.append("Rybbit")
    if re.search(r"\bBoardUI\b", speech, re.I):
        tools.append("BoardUI")
    if re.search(r"\bwitr\b", speech, re.I):
        tools.append("witr")
    if re.search(r"\bskillspector\b", speech, re.I):
        tools.append("SkillSpector")
    for overlay in extract_overlay_product_names(speech):
        if re.match(r"^(?:Run|Open|Install|Comment|Follow|Try|Download)\s+\S+$", overlay, re.I):
            continue
        if overlay not in tools:
            tools.append(overlay)
    for salvaged in salvage_product_names(text, title=title):
        if salvaged not in tools:
            tools.append(salvaged)

    links.extend(_extract_urls_from_text(speech, source="speech"))
    links.extend(extract_github_slugs(speech, source="speech"))
    if ocr_part:
        links.extend(_extract_urls_from_text(ocr_part, source="ocr"))
        links.extend(extract_github_slugs(ocr_part, source="ocr"))

    # NOTE: no brand→URL generation and no local repo lists here. Links come
    # only from literal evidence text; named-only products are resolved
    # online at card-apply time (verify.resolve_github_repo).

    def _ocr_content_tools(frame_txt: str) -> list[str]:
        """Overlays + multi-brand laundry — not CTA tip lines or zip-frame logos.

        A .zip frame promotes CamelCase overlays only (SkillSpector.zip), not
        every watermark that shares the frame (Claude). «Comment Claude» /
        «Run SEO» are UI chrome, not products. Stock laundry
        (Envato/Storyblocks/Pexels) still counts when ≥2 brands.
        """
        found: list[str] = []
        overlays = extract_overlay_product_names(frame_txt)
        found.extend(overlays)
        brand_hits = [name for name, pat in _BRAND_PATTERNS if pat.search(frame_txt)]
        has_product_file = bool(
            re.search(r"\.(zip|dmg|exe|msi|app|deb)\b", frame_txt or "", re.I)
        )
        if has_product_file:
            return _uniq(found)
        # Verb+brand CTA wrappers are chrome, not tip content
        if re.match(
            r"^\s*(?:Comment|Follow|Share|Like|Open|Run|Install|Download|Try)\s+\S+",
            frame_txt or "",
            re.I,
        ):
            return _uniq(found)
        toks = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", frame_txt or "")
        # Multi-brand laundry list = on-screen tip content
        if len(brand_hits) >= 2:
            found.extend(brand_hits)
        elif len(brand_hits) == 1 and len(toks) >= 4:
            # Long tip/headline with a brand («Claude Fabel Is Crushing…»)
            found.extend(brand_hits)
        return _uniq(found)

    timed_frames = parse_timed_ocr_block(text)
    for fr in timed_frames:
        if cta_start is not None and fr.t_sec >= cta_start:
            continue
        if _is_cta_text(fr.text or ""):
            continue
        frame_txt = fr.text or ""
        # Always harvest literal URLs/slugs from frames
        links.extend(_extract_urls_from_text(frame_txt, source="ocr"))
        links.extend(extract_github_slugs(frame_txt, source="ocr"))
        # Chrome/UI frames (GitHub buttons etc.): links only — never tools
        if is_chrome_ocr(frame_txt):
            continue
        for name in _ocr_content_tools(frame_txt):
            brands_by_t.append((fr.t_sec, name))
            if name not in tools:
                tools.append(name)
    # Untimed OCR blob: only when no timed frames (avoid re-adding logos)
    if ocr_part and not timed_frames and not is_chrome_ocr(ocr_part):
        for name in _ocr_content_tools(ocr_part):
            if name not in tools:
                tools.append(name)

    return HarvestedResources(
        tools=drop_garble_tools(_uniq(tools)),
        links=merge_link_items(links),
        brands_by_t=brands_by_t,
    )


_TOOL_NAME_WHITELIST = {name.lower() for name, _ in _BRAND_PATTERNS} | {
    "logo design skill",
}


_OK_ONSETS = {
    "br", "cr", "dr", "fr", "gr", "kr", "pr", "tr", "cl", "fl", "gl", "kl",
    "pl", "sl", "bl", "sc", "sk", "sm", "sn", "sp", "st", "sv", "sw", "tw",
    "ch", "sh", "th", "wh", "ph", "gh", "sch", "scr", "spr", "str",
}


def _camel_chunks(tok: str) -> list[str]:
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+", tok or "")
    return [p for p in parts if p]


def _chunk_is_soup(chunk: str) -> bool:
    low = (chunk or "").lower()
    if len(low) < 3 or not low.isalpha():
        return False
    vowels = sum(1 for c in low if c in "aeiouy")
    if vowels / len(low) < 0.18:
        return True
    onset = re.match(r"^[^aeiouy]+", low)
    if onset:
        o = onset.group(0)
        if len(o) >= 3:
            return True
        if len(o) == 2 and o not in _OK_ONSETS:
            return True
    if re.search(r"[^aeiouy]{5,}", low):
        return True
    return False


def is_garble_tool(name: str) -> bool:
    """OCR soup (Latinized Cyrillic) must never become a tool name.

    Shape-based, no dictionary: a plausible product name (Rybbit, BoardUI,
    ZebraForge — known or not) has pronounceable chunks; soup chunks start
    with impossible onsets (Tc-, Bc-, Hb-) or lack vowels.
    """
    key = (name or "").strip().lower()
    if not key or key in _TOOL_NAME_WHITELIST:
        return False
    name = re.sub(r"\.(zip|exe|dmg|pkg|msi|app|tar\.gz)$", "", (name or "").strip(), flags=re.I)
    if re.search(r"[А-Яа-яЁё]", name or ""):
        return False
    if "/" in key:
        return False  # slugs are links, validated as URLs elsewhere
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", name or "")
    if not tokens:
        return False
    bad = 0
    for tok in tokens:
        chunks = _camel_chunks(tok)
        if chunks and any(_chunk_is_soup(c) for c in chunks):
            bad += 1
    return bad >= 1 and bad / len(tokens) >= 0.5


def drop_garble_tools(tools: list[str]) -> list[str]:
    return [t for t in tools or [] if not is_garble_tool(t)]


_NEG_CUE = re.compile(
    r"("
    r"\bне\b|\bнет\b|\bбез\b|\bвместо\b|заменит|замен[аы]|"
    r"\bnot\b|\bno\b|\bnever\b|\bwithout\b|\binstead\b|\bkilled\b|"
    r"больше\s+не|не\s+нуж"
    r")",
    re.I,
)
_POS_CUE = re.compile(
    r"(использу|откро[йю]|открыв|запуст|установ|скача|сдела\w*\s+в|работа\w*\s+в|скажите|сказать|говорите|промпт|prompt|\buse\b|\busing\b|\bopen\b|\binstall\b|\brun\b)",
    re.I,
)


def drop_negated_tools(tools: list[str], raw_text: str | None) -> list[str]:
    """Tools mentioned only as rejected/replaced («не открывая After Effects»)
    are anti-examples, not tools. Keep when any mention is positive use.

    Negation is judged on spoken evidence only — OCR chrome («Now brag») must
    not trip English ``no``/``not`` false positives.
    """
    from app.services.grounding import spoken_blob

    blob = spoken_blob(raw_text or "") or _norm_blob(raw_text or "")
    if not blob:
        return tools
    out: list[str] = []
    for t in tools or []:
        name = (t or "").strip()
        if not name or "/" in name:
            out.append(t)
            continue
        pat = None
        for brand, bp in _BRAND_PATTERNS:
            if brand.lower() == name.lower():
                pat = bp
                break
        hits: list[int] = []
        if pat is not None:
            hits = [m.start() for m in pat.finditer(blob)]
        if not hits:
            hits = [m.start() for m in re.finditer(re.escape(name.lower()), blob)]
        if not hits:
            out.append(t)
            continue
        neg = pos = 0
        for h in hits:
            before = blob[max(0, h - 45) : h]
            after = blob[h : h + 60]
            if _NEG_CUE.search(before):
                neg += 1
            elif _POS_CUE.search(before) or _POS_CUE.search(after):
                pos += 1
        if neg and not pos:
            continue
        out.append(t)
    return out


def _link_is_grounded(link: dict, blob_nospace: str, harvest_keys: set[str]) -> bool:
    key = link_key(str(link.get("url") or ""))
    if key and key in harvest_keys:
        return True
    try:
        p = urlparse(str(link.get("url") or ""))
        hostpath = ((p.netloc or "") + (p.path or "")).lower().removeprefix("www.").rstrip("/")
    except Exception:
        return False
    return bool(hostpath) and hostpath in blob_nospace


def filter_ungrounded_tools(tools: list[str], raw_text: str | None, harvest_tools: list[str]) -> list[str]:
    """Keep tools named in speech OR harvested from content OCR (not chrome)."""
    if not raw_text:
        return [t for t in tools or [] if (t or "").strip()]
    from app.services.grounding import GroundingContext, tool_is_grounded

    ctx = GroundingContext.from_raw(raw_text)
    harvest_l = {t.lower() for t in (harvest_tools or [])}
    out: list[str] = []
    for t in tools or []:
        key = (t or "").strip()
        if not key:
            continue
        if tool_is_grounded(t, ctx) or key.lower() in harvest_l:
            out.append(t)
    return out


def _norm_blob(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е"))


def demote_logo_only_tools(tools: list[str], raw_text: str | None) -> list[str]:
    """Drop UI chrome labels that slipped past harvest (Create PR, Fork You…).

    Do not use is_chrome_ocr on the tool name alone — single-token products
    (Pexels, Brag) look like lone watermarks structurally.
    """
    if not tools:
        return []
    from app.services.grounding import spoken_blob

    spoken = spoken_blob(raw_text or "")
    out: list[str] = []
    for t in tools:
        name = (t or "").strip()
        if not name:
            continue
        if re.match(
            r"^(?:Create\s*PR|Fork\s*You|Code\s*Iss?\d*|Watch\s*\d+|YOUR\s+ASSISTANT|"
            r"Custo(?:r)?\s+Public|Custor\s+Public)\b",
            name,
            re.I,
        ):
            continue
        # Verb+label is a UI/command chip, not a product — even if ASR heard it
        if re.match(r"^(?:Run|Open|Install|Comment|Follow|Try|Download)\s+\S+$", name, re.I):
            continue
        if name.isupper() and " " in name and not re.search(r"\d", name):
            continue
        out.append(t)
    # Drop single-token stubs covered by a multi-word tool («SEO» / «Monster»)
    multi = [t for t in out if " " in (t or "")]
    if multi:
        out = [
            t
            for t in out
            if " " in (t or "")
            or not any((t or "").lower() in m.lower().split() for m in multi)
        ]
    return out


_CTA_DIGEST = re.compile(
    r"("
    r"напиш\w+\s+[«\"“]?[а-яa-z]{3,}|"
    r"пиш\w+\s+в\s+коммент|"
    r"в\s+комментари\w+|"
    r"подпис\w+|"
    r"subscribe|"
    r"like\s+and\s+subscribe|"
    r"ссылк\w+\s+в\s+шапк|"
    r"link\s+in\s+bio|"
    r"бортов\w+\s+журнал|"
    r"лидмагнит|"
    r"промпт»?\s+в\s+коммент"
    r")",
    re.I,
)


def ground_digest_lines(digest: str, *, tools: list[str], raw_text: str | None) -> str:
    """Drop digest bullets that invent entities/numbers absent from speech.

    Tools that survived spoken-grounding may appear in bullets; OCR-only brands
    and invented infra (Kafka…) do not get a free pass. Lead-magnet CTA bullets
    are stripped when other content remains.
    """
    from app.services.grounding import GroundingContext, line_is_grounded

    ctx = GroundingContext.from_raw(raw_text or "")
    # Final tools (spoken + content-OCR, chrome already stripped) may appear in
    # digest. Random Latin brands not in tools/speech still die.
    allowed: set[str] = set()
    for t in tools or []:
        allowed.add(t)
        for part in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", t or ""):
            if len(part) >= 4:  # avoid short tokens like Pro/Cut greenlighting noise
                allowed.add(part)
    invented_workflow = re.compile(
        r"("
        r"\bci\s*/\s*cd\b|\bci/cd\b|"
        r"pip\s+install|"
        r"установите\s+зависимост|"
        r"перед\s+публикацией|"
        r"безопасн\w*\s+альтернатив"
        r")",
        re.I,
    )
    kept: list[str] = []
    cta_kept: list[str] = []
    for line in digest_lines(digest):
        if not line_is_grounded(line, ctx, allowed_entities=allowed, numbers_from="spoken"):
            continue
        if invented_workflow.search(line) and not invented_workflow.search(ctx.spoken):
            continue
        if _CTA_DIGEST.search(line):
            cta_kept.append(line)
            continue
        kept.append(line)
    # Keep CTA only if it is the sole surviving content
    if not kept and cta_kept:
        kept = cta_kept[:1]
    if not kept:
        return ""
    return "\n".join(f"- {ln}" for ln in kept)


def drop_non_product_tools(
    tools: list[str],
    raw_text: str | None,
    harvest_tools: list[str] | None = None,
) -> list[str]:
    """Generic product-shape gates (no brand lists): screen fragments and
    example names from screenshots are not tools.

    Drop when: single-letter token («D DTF VC», «Q Wordpress»); trailing
    integer («Pro Max 49», «Sive 80») never spoken; multiword name with no
    significant token in speech/caption («Kiln Specialty» from a README
    screenshot — examples, not the product). Harvested OCR laundry/overlays
    may lack speech tokens and still count.
    """
    if not raw_text:
        return tools
    from app.services.grounding import GroundingContext

    ctx = GroundingContext.from_raw(raw_text)
    harvest_l = {t.lower() for t in (harvest_tools or [])}
    out: list[str] = []
    for t in tools or []:
        name = (t or "").strip()
        if not name:
            continue
        toks = name.split()
        if any(len(x) == 1 and x.isalpha() for x in toks):
            continue
        # ALL-CAPS UI labels («YOUR ASSISTANT», «CREATE PR») are chrome
        if len(toks) >= 2 and name.isupper() and not re.search(r"\d", name):
            continue
        low = name.lower()
        if re.search(r"\s\d+$", name) and low not in ctx.spoken and low not in harvest_l:
            continue
        if len(toks) >= 2:
            sig = [x.lower() for x in toks if len(x) >= 4]
            in_harvest = low in harvest_l
            # Word-boundary: «code» must not hitchhike on slide OCR leftovers
            if sig and not any(
                re.search(rf"\b{re.escape(x)}\b", ctx.spoken, re.I) for x in sig
            ):
                if not in_harvest:
                    continue
            if not sig and low not in ctx.spoken and not in_harvest:
                # Short-token overlays («Run SEO») need speech or harvest
                continue
        out.append(t)
    return out


def merge_card_resources(
    card_tools: list[str],
    card_links: list[Any],
    harvest: HarvestedResources,
    *,
    raw_text: str | None = None,
    canon: dict | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Union LLM tools/links with harvested ones; harvest fills gaps."""
    tools_raw = [normalize_brand_names(t) for t in (card_tools or [])]
    canon = canon or {}
    aliases = {str(k).lower(): str(v) for k, v in (canon.get("aliases") or {}).items()}
    if aliases:
        tools_raw = [aliases.get(t.lower(), t) for t in tools_raw]
    tools = _uniq([*tools_raw, *harvest.tools])
    # Drop obvious ASR leftovers
    tools = [t for t in tools if t.lower() not in {"rybit", "vitter", "wcrybbit"}]
    tools = [t for t in tools if not re.match(r"^ocr\b", t, re.I)]
    banned = {str(x).lower() for x in (canon.get("banned_tools") or [])}
    if banned:
        tools = [t for t in tools if t.lower() not in banned]
    tools = demote_logo_only_tools(tools, raw_text)
    if raw_text:
        tools = filter_ungrounded_tools(tools, raw_text, harvest.tools)
    tools = drop_garble_tools(tools)
    # Host/path fragments (github.com/owner) are links, not tool names
    tools = [
        t for t in tools if not re.match(r"^(?:www\.)?[\w-]+\.(?:com|io|dev|org|net|ru)/", t or "")
    ]
    if raw_text:
        tools = drop_negated_tools(tools, raw_text)
        tools = drop_non_product_tools(tools, raw_text, harvest.tools)
    # Canon repos: owner-confirmed canonical links always present
    canon_links: list[dict[str, str]] = []
    for brand, url in (canon.get("repos") or {}).items():
        if str(brand).lower() in " ".join(tools).lower() or str(brand).lower() in (raw_text or "").lower():
            canon_links.append({"url": str(url), "title": str(brand), "source": "canon"})
    llm_links = coerce_link_items(card_links, default_source="llm")
    for li in llm_links:
        if not li.get("source"):
            li["source"] = "llm"
        if li.get("title"):
            li["title"] = normalize_brand_names(li["title"])
    if raw_text:
        # LLM links are kept ONLY when the URL literally appears in evidence
        # (speech/OCR/caption) or harvest — no «well-known official pages».
        _blob_ns = re.sub(r"\s+", "", (raw_text or "").lower())
        _harvest_keys = {link_key(str(x.get("url") or "")) for x in harvest.links}
        llm_links = [
            li for li in llm_links if _link_is_grounded(li, _blob_ns, _harvest_keys)
        ]
    links = merge_link_items(harvest.links, llm_links, canon_links)
    # Drop junk OCR domains + bare generic apex (google.com etc.)
    cleaned: list[dict[str, str]] = []
    for li in links:
        try:
            p = urlparse(li.get("url") or "")
            host = p.netloc.lower().removeprefix("www.")
            path = p.path or "/"
        except Exception:
            continue
        if host in _OCR_JUNK_HOSTS:
            continue
        if host in _GENERIC_APEX_HOSTS and path in {"", "/"}:
            continue
        # Bare github.com root is not a resource — only owner/repo paths count
        if host == "github.com" and path in {"", "/"}:
            continue
        cleaned.append(li)
    return tools, cleaned


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
