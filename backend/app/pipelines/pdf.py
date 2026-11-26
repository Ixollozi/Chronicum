from __future__ import annotations

import asyncio
from pathlib import Path

from pypdf import PdfReader

from app.logging_setup import get_logger

log = get_logger(__name__)


async def extract_pdf_text(path: str) -> str:
    def _read() -> str:
        reader = PdfReader(path)
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
        if len(text) >= 80:
            return text

        # Likely scan — OCR via pdf2image + tesseract
        try:
            from pdf2image import convert_from_path
            import pytesseract
        except ImportError as e:
            log.warning("pdf_ocr_deps_missing", error=str(e))
            note = (
                "\n\n[PDF appears scanned; OCR unavailable. "
                "Install: pip install pdf2image && Poppler + Tesseract]"
            )
            return (text + note).strip() or note.strip()

        try:
            images = convert_from_path(path, dpi=200)
            ocr_parts = [pytesseract.image_to_string(img, lang="rus+eng") for img in images[:30]]
            ocr = "\n".join(ocr_parts).strip()
            return ocr or text or f"[Empty PDF: {Path(path).name}]"
        except Exception as e:
            msg = str(e)
            log.warning("pdf_ocr_fallback", error=msg[:400])
            hint = ""
            if "poppler" in msg.lower() or "pdftoppm" in msg.lower():
                hint = " (need Poppler in PATH — winget install oschwartz10612.Poppler)"
            elif "tesseract" in msg.lower():
                hint = " (need Tesseract — winget install UB-Mannheim.TesseractOCR)"
            note = f"\n\n[PDF OCR failed{hint}: {msg[:200]}]"
            return (text + note).strip()

    return await asyncio.to_thread(_read)
