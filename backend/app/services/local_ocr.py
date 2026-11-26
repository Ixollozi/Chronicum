"""Local OCR: Tesseract if available, else RapidOCR (free ONNX)."""

from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

from app.logging_setup import get_logger

log = get_logger(__name__)

_COMMON_TESSERACT = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    str(Path.home() / "AppData/Local/Programs/Tesseract-OCR/tesseract.exe"),
)

# Tokens worth keeping even when the rest of a line is Latinized-Cyrillic garble
_SALVAGE_RE = re.compile(
    r"("
    r"#\s*\d{1,2}"
    r"|https?://[^\s<>\"')\]]+"
    r"|[\w.-]+\.(?:com|io|app|org|net)\b"
    r"|[\w.-]+\.zip\b"
    r"|\b(?:facebook|slack|premiere(?:\s*pro)?|davinci(?:\s*resolve)?|final\s*cut|"
    r"capcut|envato(?:elements)?|storyblocks|pexels|unsplash|chatgpt|claude|"
    r"gemini(?:\s*omni)?|seedance|omniflash|omni\s*flash|google\s*flow|cloth|"
    r"figma|photoshop|canva|notion|pencil|midjourney|runway|artlist|descript|"
    r"opus\s*clip|motionarray|dafont|fonts\.google(?:\.com)?|creativemarket|"
    r"lumetri|report\s*card|skillspector|nvidia|cursor|github)\b"
    r"|\b\d{1,3}%"
    r"|\b\d{1,3}\s*(?:дней|друзей|сообщений|мин|час(?:ов)?)\b"
    r")",
    re.I,
)

_VOWELS_LAT = set("aeiouyAEIOUY")
_KNOWN_LAT = {
    "facebook",
    "slack",
    "premiere",
    "davinci",
    "pexels",
    "envato",
    "envatoelements",
    "storyblocks",
    "chatgpt",
    "claude",
    "gemini",
    "omni",
    "seedance",
    "omniflash",
    "cloth",
    "pencil",
    "report",
    "card",
    "lumetri",
    "figma",
    "canva",
    "capcut",
    "overlay",
    "keyframe",
    "opacity",
    "layer",
    "timeline",
    "notion",
    "flow",
    "skillspector",
    "nvidia",
    "cursor",
    "github",
    "download",
    "unsplash",
    "photoshop",
    "midjourney",
    "runway",
    "descript",
    "motionarray",
    "creativemarket",
    "dafont",
    "tenchat",
    "kwork",
    "codex",
    "logo",
    "design",
    "skill",
    "skills",
    "agent",
    "agents",
    "install",
    "tests",
    "test",
    "use",
    "music",
}


@lru_cache
def configure_tesseract() -> bool:
    """Point pytesseract at a binary if found. Return True if usable."""
    try:
        import pytesseract
    except ImportError:
        return False

    env = (os.environ.get("TESSERACT_CMD") or "").strip()
    candidates = [env] if env else []
    which = shutil.which("tesseract")
    if which:
        candidates.append(which)
    candidates.extend(_COMMON_TESSERACT)

    for c in candidates:
        if c and Path(c).exists():
            pytesseract.pytesseract.tesseract_cmd = c
            try:
                pytesseract.get_tesseract_version()
                log.info("tesseract_ready", path=c)
                return True
            except Exception:
                continue
    return False


@lru_cache
def _rapid_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


@lru_cache
def _rapid_engine_v3():
    """RapidOCR v3 (PP-OCRv5) with Cyrillic recognition — reads RU slides/screens.

    The v1 engine has no Cyrillic model and transliterates Russian into
    Latin lookalike soup; v3 cyrillic rec is the primary engine when present.
    """
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    return RapidOCR(
        params={
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.lang_type": LangRec.CYRILLIC,
            "Global.log_level": "error",
        }
    )


def _ocr_path_rapid_v3(path: str) -> str:
    engine = _rapid_engine_v3()
    out = engine(path)
    txts = getattr(out, "txts", None)
    if not txts:
        return ""
    return re.sub(r"\s+", " ", " ".join(str(t) for t in txts)).strip()


def _salvage_token_ok(tok: str) -> bool:
    """Reject Latinized-Cyrillic soup that shape-matches brand patterns.

    Real salvage: URLs/domains/#N/percents, known brand words, clean numbers.
    Soup markers: digit+letter case flips (71noAo3pHTe), unknown pure-Latin
    words (MupyioTcyHaoc), CamelCase compounds of unknown words (FalCup).
    """
    low = tok.lower()
    if any(ch in tok for ch in "./#%"):
        return True
    if re.fullmatch(r"\d{1,3}", tok):
        return True
    if re.search(r"\d", tok) and re.search(r"[a-z][A-Z]|[A-Z][a-z][A-Z]", tok):
        return False
    words = re.findall(r"[A-Za-z]+", low)
    if words and all(w in _KNOWN_LAT for w in words):
        return True
    # Multi-word known phrases (premiere pro / google flow / opus clip)
    if words and any(w in _KNOWN_LAT for w in words) and len(words) <= 3:
        return all(
            w in _KNOWN_LAT or len(w) <= 3 for w in words
        )
    return False


def ocr_salvage(text: str) -> str:
    """Keep only high-confidence tokens (brands, URLs, #N, key numbers)."""
    if not text:
        return ""
    found: list[str] = []
    seen: set[str] = set()
    for m in _SALVAGE_RE.finditer(text):
        tok = re.sub(r"\s+", " ", m.group(0)).strip()
        key = tok.lower()
        if key in seen:
            continue
        if not _salvage_token_ok(tok):
            continue
        seen.add(key)
        found.append(tok)
    return " ".join(found)


def ocr_is_garble(text: str) -> bool:
    """True when OCR looks like Latinized/mojibake Cyrillic (unusable as prose)."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned or re.fullmatch(r"@[\w.]+", cleaned):
        return True

    body = re.sub(r"@[\w.]+", " ", cleaned).strip()
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", body)
    salvaged = ocr_salvage(body)
    body_alnum = re.sub(r"[^A-Za-zА-Яа-яЁё0-9]+", "", body)
    salv_alnum = re.sub(r"[^A-Za-zА-Яа-яЁё0-9]+", "", salvaged)

    if len(letters) < 4:
        return not bool(salvaged)

    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    cyr_ratio = cyr / len(letters)
    if cyr_ratio >= 0.30:
        return False

    # Real bilingual OCR (v3 Cyrillic engine): whole Cyrillic words present and
    # few mixed-script tokens → readable text, not transliteration soup.
    _toks = cleaned.split()
    _cyr_toks = [
        t for t in _toks if re.search(r"[А-Яа-яЁё]{2,}", t) and not re.search(r"[A-Za-z]", t)
    ]
    _mixed_toks = [
        t for t in _toks if re.search(r"[А-Яа-яЁё]", t) and re.search(r"[A-Za-z]", t)
    ]
    if len(_cyr_toks) >= 2 and len(_mixed_toks) <= max(1, len(_toks) // 5):
        return False

    # Few salvageable tokens vs long Latin soup → garble
    if cyr_ratio < 0.25 and len(body_alnum) >= 8:
        if len(salv_alnum) < max(3, int(len(body_alnum) * 0.4)):
            return True

    lat_tokens = re.findall(r"[A-Za-z]{4,}", body)
    if not lat_tokens:
        return cyr_ratio < 0.15 and not salvaged

    # Pure unknown Latin with zero Cyrillic → almost always mojibake on RU reels
    if cyr_ratio < 0.05 and not salvaged:
        if all(t.lower() not in _KNOWN_LAT for t in lat_tokens):
            return True

    weird = 0
    for tok in lat_tokens:
        if tok.lower() in _KNOWN_LAT:
            continue
        vowels = sum(1 for c in tok if c in _VOWELS_LAT)
        v_ratio = vowels / max(1, len(tok))
        case_flip = bool(re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]", tok))
        if v_ratio < 0.28 or case_flip or (tok.isupper() and len(tok) >= 8 and v_ratio < 0.45):
            weird += 1

    weird_ratio = weird / max(1, len(lat_tokens))
    if weird_ratio >= 0.3 and cyr_ratio < 0.2:
        return True
    if cyr_ratio < 0.08 and len(lat_tokens) >= 2 and weird_ratio >= 0.15:
        return True
    return False


def _strip_orphan_digits(text: str) -> str:
    """Drop bare digit blobs (common RapidOCR noise like '7214'). Keep percents."""
    parts = re.split(r"(\s+)", text or "")
    out: list[str] = []
    for p in parts:
        if re.fullmatch(r"\d{3,6}", p):
            continue
        out.append(p)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def ocr_sanitize(text: str) -> str:
    """Readable OCR for storage/captions; garble → salvage tokens or empty."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"\s*@[\w.]+\s*", " ", cleaned).strip()
    if not cleaned:
        return ""
    if not ocr_is_garble(cleaned):
        return _strip_orphan_digits(cleaned)
    return _strip_orphan_digits(ocr_salvage(cleaned))


def ocr_caption_weak(text: str) -> bool:
    """Captions that are only percents/digits/brands — prefer speech instead."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return True
    if re.fullmatch(r"[\d\s.%/+×x-]+", cleaned):
        return True
    if re.fullmatch(r"(?i)report\s*card", cleaned):
        return True
    toks = re.findall(r"[A-Za-zА-Яа-яЁё0-9%]+", cleaned)
    if not toks:
        return True
    # Single brand/logo label (e.g. FACEBOOK) — keep in OCR harvest, not as caption
    if len(toks) == 1 and toks[0].lower() in _KNOWN_LAT:
        return True
    if all(t.lower() in _KNOWN_LAT for t in toks) and len(toks) <= 2:
        return True
    return False


def _ocr_path_rapid(path: str) -> str:
    # Primary: v3 Cyrillic engine; fallback: legacy v1 (en/zh only)
    try:
        text = _ocr_path_rapid_v3(path)
        if text:
            return text
    except Exception as e:
        log.warning("rapidocr_v3_failed", path=path, error=str(e)[:200])
    try:
        engine = _rapid_engine()
        result, _ = engine(str(path))
        if not result:
            return ""
        parts = []
        for item in result:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                t = str(item[1]).strip()
                if t:
                    parts.append(t)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()
    except Exception as e:
        log.warning("rapidocr_failed", path=path, error=str(e)[:200])
        return ""


def _ocr_path_tesseract(path: str) -> str:
    try:
        import pytesseract
        from PIL import Image

        try:
            text = pytesseract.image_to_string(Image.open(path), lang="rus+eng")
        except Exception:
            text = pytesseract.image_to_string(Image.open(path))
        return re.sub(r"\s+", " ", (text or "").strip())
    except Exception as e:
        log.warning("tesseract_ocr_failed", path=path, error=str(e)[:160])
        return ""


def _ocr_path(path: str) -> str:
    if configure_tesseract():
        raw = _ocr_path_tesseract(path)
        if raw:
            return raw
    return _ocr_path_rapid(path)


def ocr_image_file(path: str) -> str:
    """Return cleaned on-screen text from an image path (local/free)."""
    p = Path(path)
    if not p.exists():
        return ""

    cleaned = ocr_sanitize(_ocr_path(path))
    if len(cleaned) >= 40:
        return cleaned

    # White-on-dark designs (slides, dark UI) defeat detectors — retry inverted
    try:
        from PIL import Image, ImageOps

        img = Image.open(path).convert("RGB")
        inv = ImageOps.autocontrast(ImageOps.invert(img))
        tmp = p.with_name(p.stem + "_inv.jpg")
        inv.save(tmp, quality=92)
        try:
            alt = ocr_sanitize(_ocr_path(str(tmp)))
        finally:
            tmp.unlink(missing_ok=True)
        if len(alt) > len(cleaned):
            return alt
    except Exception as e:
        log.warning("ocr_invert_retry_failed", path=path, error=str(e)[:160])
    return cleaned
