"""Generic evidence grounding — no brand/link lists involved.

Privilege layers (not dictionaries):
- spoken (speech + caption + slides, no timed OCR) → tools, digest entities
- full (incl. OCR) → literal URLs / github slugs only

UI chrome on a screenshot may contain the words «Fork» / «Code»; that must
NOT make them tools. If the author named a product in speech, it is grounded
even when we have never heard of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TS_PREFIX = re.compile(r"^\[(?:\d{1,3}:\d{2}(?:\.\d+)?|OCR\s+\d{1,3}:\d{2}(?:\.\d+)?|Слайд\s+\d+|Видео\s+\d+)\]\s*", re.I)
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9._+#-]*")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip()


def strip_markers(text: str) -> str:
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("[On-screen") or s in ("[OCR]", "[Vision]"):
            continue
        out.append(_TS_PREFIX.sub("", s))
    return "\n".join(out)


def spoken_blob(raw_text: str | None) -> str:
    """Speech + caption + clean slides (no timed/legacy OCR dumps)."""
    try:
        from app.services.screen_atlas import strip_ocr_blocks

        return norm(strip_markers(strip_ocr_blocks(raw_text or "")))
    except Exception:
        return norm(strip_markers(raw_text or ""))


def full_blob(raw_text: str | None) -> str:
    """All evidence text including timed OCR."""
    return norm(strip_markers(raw_text or ""))


def lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


def _tokens(s: str) -> list[str]:
    return [t.strip("._+#-") for t in _WORD.findall(norm(s)) if len(t.strip("._+#-")) >= 2]


@dataclass
class GroundingContext:
    spoken: str = ""
    full: str = ""
    spoken_tokens: set[str] = field(default_factory=set)
    full_tokens: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)
    spoken_numbers: set[str] = field(default_factory=set)

    @classmethod
    def from_raw(cls, raw_text: str | None) -> "GroundingContext":
        sp = spoken_blob(raw_text)
        fu = full_blob(raw_text)
        return cls(
            spoken=sp,
            full=fu,
            spoken_tokens=set(_tokens(sp)),
            full_tokens=set(_tokens(fu)),
            numbers=extract_numbers(fu),
            spoken_numbers=extract_numbers(sp),
        )


def _token_grounded(tok: str, blob_tokens: set[str]) -> bool:
    if tok in blob_tokens:
        return True
    # Fuzzy only for long tokens (avoid chatgpt≈chatopt inventing brands)
    if len(tok) >= 8:
        limit = 1
        for bt in blob_tokens:
            if abs(len(bt) - len(tok)) <= limit and lev(tok, bt) <= limit:
                return True
    return False


def entity_grounded(name: str, ctx: GroundingContext, *, blob: str = "full") -> bool:
    """True when a claimed entity is findable in evidence (generic match)."""
    n = norm(name)
    if not n:
        return False
    if blob == "spoken":
        hay, tokens = ctx.spoken, ctx.spoken_tokens
    else:
        hay, tokens = ctx.full, ctx.full_tokens
    if n in hay:
        return True
    toks = [t for t in _tokens(n) if len(t) >= 3]
    if not toks:
        return n in hay
    return all(_token_grounded(t, tokens) for t in toks)


# --- entities ------------------------------------------------------------

_QUOTED = re.compile(r"[«\"“]([^«»\"”]{3,60})[»\"”]")
_CAMEL = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-zA-Z0-9]+)+\b")
_CAPS_SEQ = re.compile(r"\b(?:[A-ZА-ЯЁ][A-ZА-ЯЁ0-9]{2,}(?:[-\s][A-ZА-ЯЁ0-9]{2,})*)\b")
_TITLE_SEQ = re.compile(r"\b(?:[A-Z][a-zA-Z0-9.+#-]+)(?:\s+(?:[A-Z][a-zA-Z0-9.+#-]+|Pro|Max|Plus|AI|OS|Go|JS|3D)){0,3}\b")
_LATIN_WORD = re.compile(r"\b[A-Za-z][A-Za-z0-9._+#-]{2,}\b")
_STOP_LATIN = {
    "the", "and", "for", "with", "this", "that", "from", "your", "you", "can",
    "all", "how", "what", "when", "use", "using", "used", "video", "videos",
    "http", "https", "www", "com", "org", "net", "svg", "mp4", "png", "jpg",
}


def extract_entities(text: str) -> list[str]:
    """Distinctive entity candidates: quoted, CamelCase, CAPS, Latin names.

    Plain capitalized sentence-start words are ignored — they are grammar,
    not product names.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", (s or "").strip(" .,:;()[]"))
        key = s.lower()
        if len(s) >= 3 and key not in seen and key not in _STOP_LATIN:
            seen.add(key)
            out.append(s)

    for m in _QUOTED.finditer(text or ""):
        add(m.group(1))
    for m in _CAMEL.finditer(text or ""):
        add(m.group(0))
    # TitleCase Latin word = proper name (Notion, Blender…).
    # Also allow bullet/line-start («- Redis кэш») — grammar skip only after .!?
    for m in _LATIN_WORD.finditer(text or ""):
        tok = m.group(0)
        if not re.fullmatch(r"[A-Z][a-z]{3,}[A-Za-z]*", tok):
            continue
        prev = (text or "")[m.start() - 1] if m.start() > 0 else "\n"
        if prev in ".!?":
            continue
        add(tok)
    for m in _CAPS_SEQ.finditer(text or ""):
        add(m.group(0))
    for m in _TITLE_SEQ.finditer(text or ""):
        tok = m.group(0)
        # Title sequence is distinctive only with a digit, dot, or 2+ words
        if " " in tok or re.search(r"\d", tok) or "." in tok:
            add(tok)
    for m in _LATIN_WORD.finditer(text or ""):
        tok = m.group(0)
        if re.search(r"\d", tok) or "." in tok or tok.isupper():
            add(tok)
    return out


# --- numbers ---------------------------------------------------------------

_NUM = re.compile(r"\d[\d\s\u00a0]*(?:[.,]\d+)?\s*%?|\b\d+(?:[.,]\d+)?\b")


def _norm_number(s: str) -> str:
    s = (s or "").replace("\u00a0", " ").strip().rstrip("%").strip()
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)  # 1 400 → 1400
    return s.replace(",", ".")


def extract_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for m in _NUM.finditer(text or ""):
        n = _norm_number(m.group(0))
        if n and any(ch.isdigit() for ch in n):
            out.add(n)
            # Also register integer part (15.5 grounds «15»)
            if "." in n:
                out.add(n.split(".")[0])
    return out


def _number_grounded(num: str, ctx: GroundingContext) -> bool:
    if num in ctx.numbers:
        return True
    for bn in ctx.numbers:
        if _number_prefix_ok(num, bn):
            return True
    return False


# --- line / tool validation --------------------------------------------------

def line_is_grounded(
    line: str,
    ctx: GroundingContext,
    *,
    allowed_entities: set[str] | None = None,
    numbers_from: str = "spoken",
) -> bool:
    """Digest bullet: every distinctive entity/number must be in speech/caption.

    allowed_entities is a narrow escape hatch (e.g. final spoken-grounded tools),
    not a free pass for OCR chrome or invented infra brands.
    """
    allowed = {norm(a) for a in (allowed_entities or set()) if a}
    for ent in extract_entities(line):
        en = norm(ent)
        # Full-token / word-boundary only — «Pro» must not greenlight «Product Hunt»
        if any(
            en == a or re.search(rf"\b{re.escape(en)}\b", a) or re.search(rf"\b{re.escape(a)}\b", en)
            for a in allowed
            if a and len(a) >= 3
        ):
            continue
        if entity_grounded(ent, ctx, blob="spoken"):
            continue
        return False
    num_set = ctx.spoken_numbers if numbers_from == "spoken" else ctx.numbers
    for num in extract_numbers(line):
        if num in num_set:
            continue
        if any(_number_prefix_ok(num, bn) for bn in num_set):
            continue
        return False
    return True


def tool_is_grounded(tool: str, ctx: GroundingContext) -> bool:
    """Tools must be named in speech/caption — OCR UI chrome does not count."""
    return entity_grounded(tool, ctx, blob="spoken")


def is_chrome_ocr(text: str) -> bool:
    """Structural UI/chrome detector (no brand blocklists).

    GitHub/repo chrome = github.com / MIT License / Watch+Fork widget cluster,
    even when a marketing blurb shares the frame. A short brand laundry list
    (Pexels Storyblocks) is content, not chrome. A lone watermark/logo is chrome.
    """
    raw = text or ""
    has_gh = bool(re.search(r"github\.com", raw, re.I))
    has_product_cue = bool(
        re.search(r"\.(zip|dmg|exe|msi|app|deb)\b|https?://|download\b", raw, re.I)
    )
    toks = _WORD.findall(raw)
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    phrases = sum(1 for ln in lines if len(re.findall(r"\S+", ln)) >= 4)
    # Lone watermark/logo token (Claude, FACEBOOK) — not a tip laundry list
    if len(toks) == 1 and not has_gh and not has_product_cue:
        return True
    # Percent / metric-only frames
    if toks and all(re.fullmatch(r"\d+(?:\.\d+)?%?", t) for t in toks):
        return True
    # Short GitHub action chrome («Create PR», «Watch 15»)
    if len(toks) <= 6 and re.search(
        r"\b(?:Create\s*PR|Pull\s*requests?|Watch\s+\d+|Issues?\s+\d+)\b", raw, re.I
    ):
        return True
    # Instagram engagement chrome row
    ig_hits = len(
        re.findall(
            r"\b(?:Follow|Share|Like|Comment|Original\s+audio|More\s+posts|Send\s+message)\b",
            raw,
            re.I,
        )
    )
    if ig_hits >= 2 and phrases <= 1 and not has_product_cue:
        return True
    # IDE / editor menu soup
    ide_hits = len(
        re.findall(r"\b(?:File|Edit|View|Terminal|Help|Debug|Window|Selection)\b", raw, re.I)
    )
    if ide_hits >= 4 and phrases == 0:
        return True
    # Repo-page widget cluster (survives long README / tagline prose)
    ui_hits = len(
        re.findall(
            r"\b(?:Code|Issues?|Watch|Fork|Stars?|Pull|License|Create\s*PR|MIT)\b",
            raw,
            re.I,
        )
    )
    if ui_hits >= 3 and (
        has_gh
        or re.search(r"\bMIT\s+License\b", raw, re.I)
        or re.search(r"\b\d+(?:\.\d+)?k\b", raw, re.I)
        or re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", raw)
    ):
        return True
    if has_gh and len(toks) >= 2 and len(toks) < 5:
        return True
    if len(toks) < 5:
        return False
    short = sum(1 for t in toks if 2 <= len(t) <= 8)
    short_ratio = short / max(1, len(toks))
    if has_gh and short_ratio >= 0.5 and phrases <= 2:
        return True
    # Non-github dense UI soup (no sentences, many tiny tokens)
    if not has_gh and short_ratio >= 0.8 and phrases == 0 and len(toks) >= 12:
        return True
    return False


def _number_prefix_ok(claimed: str, evidence: str) -> bool:
    """Reject year→prefix leaks: spoken 2024 must not ground digest «202»."""
    if not claimed or not evidence:
        return False
    if claimed == evidence:
        return True
    # Allow grouping spaces already normalized away; require equal numeric value
    # or evidence is a longer decimal of the same integer stem with a dot.
    try:
        if float(claimed) == float(evidence):
            return True
    except ValueError:
        pass
    # 15.5 may ground 15; 2024 must NOT ground 202
    if "." in evidence and evidence.split(".")[0] == claimed:
        return True
    if "." in claimed and claimed.split(".")[0] == evidence:
        return True
    return False
