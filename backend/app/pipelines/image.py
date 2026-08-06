from __future__ import annotations

import asyncio

import pytesseract
from PIL import Image

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)


async def extract_image_text(path: str) -> str:
    def _ocr() -> str:
        try:
            img = Image.open(path)
            return pytesseract.image_to_string(img).strip()
        except Exception as e:
            log.warning("image_ocr_failed", path=path, error=str(e)[:300])
            return ""

    ocr_text = await asyncio.to_thread(_ocr)
    parts: list[str] = []
    if ocr_text:
        parts.append(f"[OCR]\n{ocr_text}")

    # Vision if OCR empty/thin (screenshots without selectable text)
    if len(ocr_text) < 40:
        from app.ai.vision import describe_image

        desc = await describe_image(path, get_settings())
        if desc:
            parts.append(f"[Vision]\n{desc}")

    if parts:
        return "\n\n".join(parts)
    return (
        "[Image with no detectable text and vision unavailable — "
        "install Tesseract and/or set GOOGLE_AI_API_KEY for vision]"
    )
