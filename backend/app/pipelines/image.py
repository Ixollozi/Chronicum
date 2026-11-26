from __future__ import annotations

import asyncio

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)


async def extract_image_text(path: str) -> str:
    def _ocr() -> str:
        try:
            from app.services.local_ocr import ocr_image_file

            return ocr_image_file(path)
        except Exception as e:
            log.warning("image_ocr_failed", path=path, error=str(e)[:300])
            return ""

    ocr_text = await asyncio.to_thread(_ocr)
    parts: list[str] = []
    if ocr_text:
        parts.append(f"[OCR]\n{ocr_text}")

    # Vision transcription when OCR is thin/garbled (RapidOCR mangles Cyrillic
    # on designed slides — vision reads them exactly, URLs included)
    if len(ocr_text) < 120:
        from app.ai.vision import transcribe_image_text

        vision_text = await transcribe_image_text(path, get_settings())
        if vision_text and len(vision_text) > len(ocr_text):
            parts.append(f"[Vision]\n{vision_text}")

    if parts:
        return "\n\n".join(parts)
    return (
        "[Image with no detectable text and vision unavailable — "
        "install Tesseract and/or set GOOGLE_AI_API_KEY for vision]"
    )
