"""PDF ingestion, text/structure extraction, link harvesting, and OCR/vision fallback."""

from __future__ import annotations

import asyncio
import re
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from uuid import uuid4

import httpx
from pypdf import PdfReader

from app.config import Settings, get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_DEFAULT_PYPDF_READER = PdfReader

MAX_PDF_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB cap for remote PDF URLs
MAX_REDIRECTS = 5
MAX_OCR_PAGES = 25
MIN_PAGE_TEXT_CHARS = 40
MIN_DOC_TEXT_CHARS = 80

_PAGE_NUM_ONLY_RE = re.compile(
    r"^(?:(?:стр\.?|страница|page|p\.?)\s*)?\d{1,5}(?:\s*[/|–—-]\s*\d{1,5})?$",
    re.I,
)
_LIST_START_RE = re.compile(
    r"^(?:[•●▪■◦▪▸►\-–—*]|\d{1,3}[.)]|[a-zA-Zа-яА-ЯёЁ]\))\s+"
)
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_HYPHEN_WRAP_RE = re.compile(r"([A-Za-zА-Яа-яЁё]{2,})-\n([a-zа-яё]{2,})")
_WHITESPACE_RUN_RE = re.compile(r"[ \t\f\v]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_FIELD_LABEL_RE = re.compile(
    r"^(?:Что делает|Практические кейсы|Ссылка|Ссылки|Исходные данные|"
    r"Правильный порядок|Результат|Цель|Разделы|Быстрый выбор|Вывод|Итог|"
    r"Примечание|Важно|Пример|Примеры|Overview|Summary|Links?|Notes?)\b",
    re.I,
)


@dataclass
class PdfExtractionResult:
    text: str
    title: str | None = None
    author: str | None = None
    page_count: int = 0
    links: list[str] = field(default_factory=list)
    used_ocr: bool = False
    ocr_pages: int = 0
    is_scanned: bool = False
    warnings: list[str] = field(default_factory=list)


def _clean_unicode(text: str) -> str:
    if not text:
        return ""
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "").replace("\u00ad", "")
    # Normalize compatibility ligatures (fi, fl, etc.) without breaking Cyrillic
    s = unicodedata.normalize("NFKC", s)
    lines = [_WHITESPACE_RUN_RE.sub(" ", ln).strip() for ln in s.split("\n")]
    return "\n".join(lines).strip()


def _unwrap_block_text(text: str) -> str:
    """Repair PDF hyphenation and soft line-wraps inside a text block while keeping lists/headings."""
    cleaned = _clean_unicode(text)
    if not cleaned:
        return ""
    cleaned = _HYPHEN_WRAP_RE.sub(r"\1\2", cleaned)
    raw_lines = [ln for ln in cleaned.split("\n") if ln]
    if len(raw_lines) <= 1:
        return cleaned

    merged: list[str] = []
    for ln in raw_lines:
        if not merged:
            merged.append(ln)
            continue
        prev = merged[-1]
        is_list_item = bool(_LIST_START_RE.match(ln))
        is_field_label = bool(_FIELD_LABEL_RE.match(ln))
        prev_is_field_label = bool(_FIELD_LABEL_RE.match(prev)) and len(prev) <= 45
        prev_ends_sentence = prev[-1] in ".!?:;…"
        starts_lower = ln[0].islower()
        # Keep explicit list items, field labels, and distinct short headings on separate lines
        if is_list_item or is_field_label:
            merged.append(ln)
        elif prev_is_field_label:
            merged[-1] = f"{prev}: {ln}" if not prev.endswith(":") else f"{prev} {ln}"
        elif starts_lower or (not prev_ends_sentence and len(prev) >= 25 and not ln.isupper()):
            merged[-1] = f"{prev} {ln}"
        else:
            merged.append(ln)
    return "\n".join(merged)


def _normalize_margin_sig(text: str) -> str:
    s = _clean_unicode(text).replace("\n", " ")
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _is_meaningful_title(raw: str | None) -> str | None:
    s = _clean_unicode(raw or "").replace("\n", " ").strip()
    if not s or len(s) < 3:
        return None
    low = s.lower()
    if low in {"untitled", "document", "microsoft word", "presentation", "slide 1", "без названия"}:
        return None
    if re.fullmatch(r"[0-9a-f]{24,}(?:\.pdf)?", low):
        return None
    return s[:220]


def _normalize_uri(uri: str | None) -> str | None:
    s = (uri or "").strip()
    if not s:
        return None
    parsed = urlparse(s)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return s


def _rect_intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float], pad: float = 2.0) -> bool:
    return not (
        a[2] + pad < b[0]
        or b[2] + pad < a[0]
        or a[3] + pad < b[1]
        or b[3] + pad < a[1]
    )


def _format_toc(toc: list[Any], *, max_items: int = 25) -> str:
    lines: list[str] = []
    for entry in toc[:max_items]:
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            continue
        level = max(1, int(entry[0] or 1))
        title = _clean_unicode(str(entry[1] or "")).replace("\n", " ").strip()
        page_num = entry[2]
        if not title:
            continue
        indent = "  " * min(level - 1, 3)
        lines.append(f"{indent}- {title} (стр. {page_num})")
    return "\n".join(lines)


def _build_document_header(
    *,
    title: str | None,
    author: str | None,
    page_count: int,
    toc_block: str = "",
) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"Документ: {title}")
    if author:
        parts.append(f"Автор: {author}")
    if page_count > 0:
        parts.append(f"Страниц: {page_count}")
    if toc_block:
        parts.append(f"Оглавление:\n{toc_block}")
    return "\n".join(parts)


async def _gemini_ocr_page(image_path: str, *, settings: Settings, session=None) -> str:
    """Transcribe a rendered scanned PDF page with Gemini vision (pooled DB keys or .env)."""
    import google.generativeai as genai

    from app.ai import _GEMINI_LOCK

    api_keys: list[str] = []
    if session is not None:
        try:
            from app.services.key_pool import acquire_keys

            pooled = await acquire_keys(session, "google")
            api_keys.extend(pk.api_key for pk in pooled if pk.api_key)
        except Exception as exc:
            log.warning("pdf_gemini_pool_lookup_failed", error=str(exc)[:200])
    if settings.google_ai_api_key.strip() and settings.google_ai_api_key.strip() not in api_keys:
        api_keys.append(settings.google_ai_api_key.strip())

    if not api_keys:
        return ""

    prompt = (
        "Transcribe ALL text visible on this PDF page accurately, preserving headings, "
        "lists, tables, numbers, product names, and every URL character-for-character. "
        "Keep the original language (Russian/English). Plain text only, no commentary."
    )
    img_bytes = await asyncio.to_thread(Path(image_path).read_bytes)

    for key in api_keys:
        def _call(k: str = key) -> str:
            with _GEMINI_LOCK:
                genai.configure(api_key=k)
                model = genai.GenerativeModel(settings.google_ai_model)
                resp = model.generate_content(
                    [prompt, {"mime_type": "image/png", "data": img_bytes}]
                )
                return (getattr(resp, "text", None) or "").strip()

        try:
            text = await asyncio.wait_for(asyncio.to_thread(_call), timeout=60.0)
            if text:
                return _clean_unicode(text)
        except Exception as exc:
            log.warning("pdf_gemini_ocr_failed", error=str(exc)[:240])
            continue
    return ""


async def _ocr_rendered_page(
    png_bytes: bytes,
    *,
    page_num: int,
    settings: Settings,
    session=None,
) -> str:
    """OCR a single rendered PDF page: local OCR (Tesseract/RapidOCR v3) → Gemini vision fallback."""
    with tempfile.NamedTemporaryFile(prefix=f"chronicum_pdf_p{page_num}_", suffix=".png", delete=False) as tf:
        tmp_path = tf.name
        tf.write(png_bytes)

    try:
        def _local_ocr() -> str:
            try:
                from app.services.local_ocr import ocr_image_file

                return _clean_unicode(ocr_image_file(tmp_path))
            except Exception as exc:
                log.warning("pdf_local_ocr_failed", page=page_num, error=str(exc)[:240])
                return ""

        local_text = await asyncio.to_thread(_local_ocr)
        if len(local_text) >= MIN_PAGE_TEXT_CHARS:
            return local_text

        vision_text = await _gemini_ocr_page(tmp_path, settings=settings, session=session)
        if len(vision_text) > len(local_text):
            return vision_text
        return local_text
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _extract_with_pymupdf_sync(path: str) -> dict[str, Any]:
    """Read structure, blocks, hyperlinks, metadata, TOC, and candidate scan pages via PyMuPDF."""
    import pymupdf

    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ValueError(f"Не удалось открыть PDF (файл повреждён или не является PDF): {exc}") from exc

    try:
        if doc.needs_pass:
            if not doc.authenticate(""):
                raise ValueError("PDF защищён паролем и не может быть прочитан без пароля.")

        meta = doc.metadata or {}
        title = _is_meaningful_title(meta.get("title"))
        author = _clean_unicode(meta.get("author") or "").replace("\n", " ").strip() or None
        toc_block = _format_toc(doc.get_toc() or [])
        page_count = len(doc)

        raw_pages: list[dict[str, Any]] = []
        top_margin_sigs: Counter[str] = Counter()
        bot_margin_sigs: Counter[str] = Counter()

        for idx, page in enumerate(doc, 1):
            rect = page.rect
            h = float(rect.height or 842.0)
            blocks_raw = page.get_text("blocks") or []
            # Text blocks only (type == 0)
            text_blocks = [b for b in blocks_raw if len(b) >= 7 and b[6] == 0 and str(b[4] or "").strip()]
            links_raw = page.get_links() or []
            page_links: list[tuple[tuple[float, float, float, float], str]] = []
            for lnk in links_raw:
                uri = _normalize_uri(lnk.get("uri"))
                if not uri:
                    continue
                lrect = lnk.get("from")
                if lrect is not None:
                    box = (float(lrect.x0), float(lrect.y0), float(lrect.x1), float(lrect.y1))
                else:
                    box = (0.0, 0.0, 0.0, 0.0)
                page_links.append((box, uri))

            block_items: list[dict[str, Any]] = []
            for b in text_blocks:
                box = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                txt = _clean_unicode(str(b[4] or ""))
                if not txt:
                    continue
                is_top = box[1] <= h * 0.12 and "\n" not in txt and len(txt) <= 120
                is_bot = box[3] >= h * 0.88 and "\n" not in txt and len(txt) <= 120
                sig = _normalize_margin_sig(txt)
                if is_top and sig:
                    top_margin_sigs[sig] += 1
                if is_bot and sig:
                    bot_margin_sigs[sig] += 1
                block_items.append(
                    {
                        "box": box,
                        "text": txt,
                        "is_top": is_top,
                        "is_bot": is_bot,
                        "sig": sig,
                    }
                )

            has_images = bool(page.get_images(full=False))
            raw_pages.append(
                {
                    "page_num": idx,
                    "blocks": block_items,
                    "links": page_links,
                    "has_images": has_images,
                }
            )

        # Running headers/footers repeated across >=60% of pages (for 3+ page docs)
        repeat_threshold = max(3, int(page_count * 0.6)) if page_count >= 3 else page_count + 1
        repeated_top = {sig for sig, cnt in top_margin_sigs.items() if cnt >= repeat_threshold}
        repeated_bot = {sig for sig, cnt in bot_margin_sigs.items() if cnt >= repeat_threshold}

        pages_out: list[dict[str, Any]] = []
        all_links: list[str] = []
        seen_global_links: set[str] = set()
        ocr_renders: dict[int, bytes] = {}
        ocr_budget = MAX_OCR_PAGES

        for pdata in raw_pages:
            pno = pdata["page_num"]
            page_links = pdata["links"]
            attached_uris: set[str] = set()
            page_block_texts: list[str] = []

            for blk in pdata["blocks"]:
                txt = blk["text"]
                sig = blk["sig"]
                if (blk["is_top"] or blk["is_bot"]) and _PAGE_NUM_ONLY_RE.match(txt):
                    continue
                if blk["is_top"] and sig in repeated_top:
                    continue
                if blk["is_bot"] and sig in repeated_bot:
                    continue

                unwrapped = _unwrap_block_text(txt)
                if not unwrapped:
                    continue

                # Inline clickable URIs overlapping this text block
                block_uris: list[str] = []
                for lbox, uri in page_links:
                    if _rect_intersects(blk["box"], lbox):
                        attached_uris.add(uri)
                        if uri not in unwrapped and uri not in block_uris:
                            block_uris.append(uri)
                if block_uris:
                    unwrapped = f"{unwrapped} ({', '.join(block_uris)})"

                page_block_texts.append(unwrapped)

            # Collect any page URIs not matched to a text block
            leftover_uris: list[str] = []
            for _lbox, uri in page_links:
                if uri not in seen_global_links:
                    seen_global_links.add(uri)
                    all_links.append(uri)
                if uri not in attached_uris and uri not in leftover_uris:
                    leftover_uris.append(uri)

            body = "\n\n".join(page_block_texts).strip()
            if leftover_uris:
                body = (
                    f"{body}\n\nСсылки на странице: " + ", ".join(leftover_uris)
                    if body
                    else "Ссылки на странице: " + ", ".join(leftover_uris)
                )

            # Also harvest literal URLs present in text
            for m in _URL_RE.finditer(body):
                u = _normalize_uri(m.group(0).rstrip(".,;:!?)]"))
                if u and u not in seen_global_links:
                    seen_global_links.add(u)
                    all_links.append(u)

            needs_ocr = len(re.sub(r"\s+", "", body)) < MIN_PAGE_TEXT_CHARS and (
                pdata["has_images"] or not body
            )
            if needs_ocr and ocr_budget > 0:
                try:
                    pix = doc[pno - 1].get_pixmap(dpi=200)
                    ocr_renders[pno] = pix.tobytes("png")
                    ocr_budget -= 1
                except Exception as exc:
                    log.warning("pdf_render_page_failed", page=pno, error=str(exc)[:200])

            pages_out.append(
                {
                    "page_num": pno,
                    "text": body,
                    "needs_ocr": needs_ocr,
                }
            )

        return {
            "title": title,
            "author": author,
            "toc_block": toc_block,
            "page_count": page_count,
            "pages": pages_out,
            "links": all_links,
            "ocr_renders": ocr_renders,
        }
    finally:
        doc.close()


def _extract_with_pypdf_sync(path: str) -> PdfExtractionResult:
    """Fallback extractor using pypdf (+ legacy pdf2image/pytesseract OCR when scanned)."""
    try:
        reader = PdfReader(path)
    except Exception as exc:
        raise ValueError(f"Не удалось открыть PDF (файл повреждён или не является PDF): {exc}") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            ok = reader.decrypt("")
        except Exception:
            ok = 0
        if not ok:
            raise ValueError("PDF защищён паролем и не может быть прочитан без пароля.")

    meta = getattr(reader, "metadata", None) or {}
    title = _is_meaningful_title(meta.get("/Title") if hasattr(meta, "get") else None)
    author_raw = meta.get("/Author") if hasattr(meta, "get") else None
    author = _clean_unicode(str(author_raw or "")).replace("\n", " ").strip() or None

    pages = list(reader.pages)
    page_count = len(pages)
    page_texts: list[str] = []
    all_links: list[str] = []
    seen_links: set[str] = set()

    for idx, page in enumerate(pages, 1):
        txt = _unwrap_block_text(page.extract_text() or "")
        page_uris: list[str] = []
        annots = page.get("/Annots") if hasattr(page, "get") else None
        if annots:
            for a in annots:
                try:
                    obj = a.get_object() if hasattr(a, "get_object") else a
                    act = obj.get("/A") if hasattr(obj, "get") else None
                    if act and "/URI" in act:
                        uri = _normalize_uri(str(act["/URI"]))
                        if uri:
                            if uri not in seen_links:
                                seen_links.add(uri)
                                all_links.append(uri)
                            if uri not in txt and uri not in page_uris:
                                page_uris.append(uri)
                except Exception:
                    continue
        if page_uris:
            txt = f"{txt}\n\nСсылки на странице: " + ", ".join(page_uris) if txt else "Ссылки на странице: " + ", ".join(page_uris)
        page_texts.append(txt)

    combined = "\n\n".join(
        f"--- Страница {i} / {page_count} ---\n{t}" if page_count > 1 else t
        for i, t in enumerate(page_texts, 1)
        if t.strip()
    ).strip()

    if len(combined) >= MIN_DOC_TEXT_CHARS:
        header = _build_document_header(title=title, author=author, page_count=page_count)
        full = f"{header}\n\n{combined}" if header else combined
        return PdfExtractionResult(
            text=_MULTI_NEWLINE_RE.sub("\n\n", full).strip(),
            title=title,
            author=author,
            page_count=page_count,
            links=all_links,
        )

    # Scanned PDF fallback via pdf2image + pytesseract (preserves lang="rus+eng" contract)
    warnings: list[str] = []
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as e:
        log.warning("pdf_ocr_deps_missing", error=str(e))
        note = (
            "[PDF не содержит текстового слоя (похоже на скан); OCR недоступен. "
            "Установите pymupdf или pdf2image + Poppler + Tesseract]"
        )
        warnings.append(str(e))
        return PdfExtractionResult(
            text=(combined + "\n\n" + note).strip() if combined else note,
            title=title,
            author=author,
            page_count=page_count,
            links=all_links,
            is_scanned=True,
            warnings=warnings,
        )

    try:
        images = convert_from_path(path, dpi=200)
        ocr_parts = [
            _clean_unicode(pytesseract.image_to_string(img, lang="rus+eng"))
            for img in images[:MAX_OCR_PAGES]
        ]
        ocr_blocks = [
            f"--- Страница {i} / {len(images)} (OCR) ---\n{t}" if len(images) > 1 else t
            for i, t in enumerate(ocr_parts, 1)
            if t.strip()
        ]
        ocr = "\n\n".join(ocr_blocks).strip()
        text = ocr or combined or f"[Пустой PDF: {Path(path).name}]"
        return PdfExtractionResult(
            text=text,
            title=title,
            author=author,
            page_count=page_count or len(images),
            links=all_links,
            used_ocr=bool(ocr),
            ocr_pages=sum(1 for t in ocr_parts if t.strip()),
            is_scanned=True,
        )
    except Exception as e:
        msg = str(e)
        log.warning("pdf_ocr_fallback", error=msg[:400])
        hint = ""
        if "poppler" in msg.lower() or "pdftoppm" in msg.lower():
            hint = " (нужен Poppler в PATH)"
        elif "tesseract" in msg.lower():
            hint = " (нужен Tesseract)"
        note = f"[PDF OCR не удался{hint}: {msg[:200]}]"
        warnings.append(msg[:200])
        return PdfExtractionResult(
            text=(combined + "\n\n" + note).strip() if combined else note,
            title=title,
            author=author,
            page_count=page_count,
            links=all_links,
            is_scanned=True,
            warnings=warnings,
        )


def _pymupdf_available() -> bool:
    try:
        import pymupdf  # noqa: F401

        return True
    except ImportError:
        return False


async def extract_pdf_document(
    path: str,
    *,
    settings: Settings | None = None,
    session=None,
) -> PdfExtractionResult:
    """Extract structured UTF-8 text, metadata, hyperlinks, and OCR (if scanned) from a PDF."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"PDF file not found: {path}")

    cfg = settings or get_settings()

    # Respect unit tests that monkeypatch PdfReader directly
    use_pymupdf = _pymupdf_available() and (PdfReader is _DEFAULT_PYPDF_READER)

    if not use_pymupdf:
        return await asyncio.to_thread(_extract_with_pypdf_sync, str(p))

    try:
        data = await asyncio.to_thread(_extract_with_pymupdf_sync, str(p))
    except ValueError:
        raise
    except Exception as exc:
        log.warning("pymupdf_extract_failed_fallback_pypdf", path=str(p), error=str(exc)[:300])
        return await asyncio.to_thread(_extract_with_pypdf_sync, str(p))

    pages = data["pages"]
    page_count = data["page_count"]
    ocr_renders: dict[int, bytes] = data["ocr_renders"]
    native_chars = sum(len(re.sub(r"\s+", "", pg["text"])) for pg in pages)
    is_scanned = native_chars < MIN_DOC_TEXT_CHARS

    used_ocr = False
    ocr_pages = 0
    warnings: list[str] = []

    if ocr_renders:
        for pg in pages:
            pno = pg["page_num"]
            png_bytes = ocr_renders.get(pno)
            if not png_bytes:
                continue
            ocr_text = await _ocr_rendered_page(
                png_bytes,
                page_num=pno,
                settings=cfg,
                session=session,
            )
            if ocr_text:
                used_ocr = True
                ocr_pages += 1
                pg["ocr"] = True
                pg["text"] = f"{pg['text']}\n\n{ocr_text}".strip() if pg["text"] else ocr_text

    if page_count > MAX_OCR_PAGES and is_scanned:
        warnings.append(f"OCR ограничен первыми {MAX_OCR_PAGES} из {page_count} страниц.")

    rendered_pages: list[str] = []
    for pg in pages:
        body = (pg["text"] or "").strip()
        if not body:
            continue
        pno = pg["page_num"]
        tag = " (OCR)" if pg.get("ocr") else ""
        if page_count > 1:
            rendered_pages.append(f"--- Страница {pno} / {page_count}{tag} ---\n{body}")
        else:
            rendered_pages.append(f"[OCR]\n{body}" if pg.get("ocr") else body)

    body_text = "\n\n".join(rendered_pages).strip()
    if not body_text:
        note = (
            f"[PDF не содержит текстового слоя (похоже на скан/изображения, страниц: {page_count}); "
            "распознать текст автоматически не удалось]"
        )
        warnings.append("empty_or_unreadable_scan")
        body_text = note

    header = _build_document_header(
        title=data["title"],
        author=data["author"],
        page_count=page_count,
        toc_block=data["toc_block"],
    )
    if warnings:
        warn_block = "\n".join(f"[Примечание: {w}]" for w in warnings if w != "empty_or_unreadable_scan")
        if warn_block:
            body_text = f"{body_text}\n\n{warn_block}"

    full_text = f"{header}\n\n{body_text}" if header else body_text
    full_text = _MULTI_NEWLINE_RE.sub("\n\n", full_text).strip()

    return PdfExtractionResult(
        text=full_text,
        title=data["title"],
        author=data["author"],
        page_count=page_count,
        links=data["links"],
        used_ocr=used_ocr,
        ocr_pages=ocr_pages,
        is_scanned=is_scanned,
        warnings=warnings,
    )


async def extract_pdf_text(
    path: str,
    *,
    settings: Settings | None = None,
    session=None,
) -> str:
    """Backward-compatible wrapper returning the extracted PDF text."""
    result = await extract_pdf_document(path, settings=settings, session=session)
    return result.text


def _filename_hint_from_url(url: str, content_disposition: str | None = None) -> str:
    if content_disposition:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', content_disposition, re.I)
        if m:
            candidate = unquote(m.group(1)).strip().strip('"')
            if candidate:
                return Path(candidate).name
    parsed = urlparse(url)
    leaf = unquote(Path(parsed.path).name or "").strip()
    if leaf:
        return leaf if leaf.lower().endswith(".pdf") else f"{leaf}.pdf"
    return "document.pdf"


async def download_pdf_url(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int = MAX_PDF_DOWNLOAD_BYTES,
) -> tuple[str, str]:
    """Safely download a public HTTP(S) PDF URL to `dest_dir` with SSRF and magic-byte validation."""
    from app.pipelines.article import validate_public_http_url

    current_url = await validate_public_http_url(url)
    headers = {"User-Agent": "ChronicumBot/0.1", "Accept": "application/pdf,*/*;q=0.8"}
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid4().hex}.pdf"

    async with httpx.AsyncClient(follow_redirects=False, timeout=90) as client:
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
                ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                cl_hdr = resp.headers.get("content-length")
                if cl_hdr:
                    try:
                        if int(cl_hdr) > max_bytes:
                            raise ValueError(f"PDF exceeds {max_bytes} bytes limit")
                    except ValueError as exc:
                        if "exceeds" in str(exc):
                            raise

                total = 0
                head_buf = bytearray()
                fp = await asyncio.to_thread(open, dest, "wb")
                try:
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"PDF exceeds {max_bytes} bytes limit")
                        if len(head_buf) < 1024:
                            head_buf.extend(chunk[: 1024 - len(head_buf)])
                        await asyncio.to_thread(fp.write, chunk)
                except Exception:
                    await asyncio.to_thread(fp.close)
                    dest.unlink(missing_ok=True)
                    raise
                else:
                    await asyncio.to_thread(fp.close)

                head_bytes = bytes(head_buf)
                has_pdf_magic = b"%PDF-" in head_bytes
                if not has_pdf_magic and ct not in {"application/pdf", "application/x-pdf"}:
                    dest.unlink(missing_ok=True)
                    raise ValueError(f"URL did not return a valid PDF (Content-Type: {ct or 'unknown'})")
                if not has_pdf_magic:
                    dest.unlink(missing_ok=True)
                    raise ValueError("Downloaded file does not contain a valid PDF header (%PDF-)")

                hint = _filename_hint_from_url(current_url, resp.headers.get("content-disposition"))
                return str(dest), hint
        raise ValueError(f"Too many redirects (>{MAX_REDIRECTS}) while fetching PDF")
