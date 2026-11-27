"""Focused unit tests for PDF extraction, OCR/vision fallback, direct PDF URLs, and large-PDF LLM routing."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image

from app.ai import (
    GroqLLMClient,
    LLMClient,
    PayloadTooLargeError,
    analyze_material,
    estimate_tokens,
    split_document_chunks,
    with_llm_fallback,
)
from app.ai.schemas import KnowledgeCard, LLMResult, UsefulnessSignals
from app.config import Settings
from app.pipelines import article as article_mod
from app.pipelines import pdf as pdf_mod
from app.services.ai_limits import is_transient_rate_limit_error, key_in_cooldown, reset_limits_for_tests
from app.services.sources import classify_url


def _make_multipage_cyrillic_pdf(path: Path) -> None:
    """Create a 4-page Cyrillic PDF with metadata, repeated headers/footers, hyphenation, and links."""
    doc = pymupdf.open()
    doc.set_metadata(
        {
            "title": "Практический справочник по OSINT в Telegram",
            "author": "@magerdev1",
        }
    )
    font = pymupdf.Font("helv")
    # Use built-in Cyrillic-capable system font on Windows/Linux or DejaVu if present;
    # PyMuPDF's insert_htmlbox renders UTF-8 Cyrillic cleanly without external font files.
    for pno in range(1, 5):
        page = doc.new_page(width=595, height=842)
        # Running header (top margin)
        page.insert_htmlbox(
            pymupdf.Rect(40, 15, 555, 45),
            "<div style='font-size:9pt'>Практический справочник по OSINT в Telegram</div>",
        )
        # Body content
        body_html = (
            f"<h3>Раздел {pno}. Инструменты поиска</h3>"
            f"<p>Инстру-<br>менты для анализа каналов на странице {pno} позволяют "
            f"быстро находить первоисточник публикации.</p>"
            f"<p>• Первый шаг проверки на странице {pno}<br>• Второй шаг проверки</p>"
            f"<p>Ссылка<br>Открыть официальный ресурс</p>"
        )
        page.insert_htmlbox(pymupdf.Rect(40, 110, 555, 420), body_html)
        # Clickable hyperlink annotation overlapping the link block
        page.insert_link(
            {
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(40, 110, 555, 420),
                "uri": f"https://example.org/tool-{pno}",
            }
        )
        # Running footer with page number (bottom margin)
        page.insert_htmlbox(
            pymupdf.Rect(40, 795, 555, 830),
            f"<div style='font-size:9pt'>Автор: @magerdev1 | {pno}</div>",
        )
    doc.save(str(path))
    doc.close()
    _ = font


def _make_scanned_image_pdf(path: Path) -> None:
    """Create a 1-page PDF containing only a raster image and no text layer."""
    img = Image.new("RGB", (400, 200), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=200)
    page.insert_image(pymupdf.Rect(0, 0, 400, 200), stream=buf.getvalue())
    doc.save(str(path))
    doc.close()


def _make_encrypted_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Secret PDF content")
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="topsecret",
        owner_pw="ownerpass",
    )
    doc.close()


@pytest.mark.asyncio
async def test_multipage_cyrillic_pdf_structure_links_and_dedup(tmp_path: Path):
    pdf_path = tmp_path / "osint_guide.pdf"
    _make_multipage_cyrillic_pdf(pdf_path)

    res = await pdf_mod.extract_pdf_document(str(pdf_path))

    assert res.page_count == 4
    assert res.title == "Практический справочник по OSINT в Telegram"
    assert res.author == "@magerdev1"
    assert not res.is_scanned
    assert not res.used_ocr

    # Page boundaries present
    assert "--- Страница 1 / 4 ---" in res.text
    assert "--- Страница 4 / 4 ---" in res.text

    # Cyrillic preserved and hyphenated word repaired
    assert "Инструменты для анализа каналов" in res.text
    assert "Инстру-\nменты" not in res.text

    # Running footer ("Автор: @magerdev1 | 1..4") stripped across all 4 pages
    assert "Автор: @magerdev1 | 2" not in res.text
    assert "Автор: @magerdev1 | 3" not in res.text

    # All 4 clickable hyperlink annotations extracted and inlined into text
    assert res.links == [
        "https://example.org/tool-1",
        "https://example.org/tool-2",
        "https://example.org/tool-3",
        "https://example.org/tool-4",
    ]
    for url in res.links:
        assert url in res.text


@pytest.mark.asyncio
async def test_scanned_pdf_uses_local_ocr_fallback(tmp_path: Path, monkeypatch):
    scan_pdf = tmp_path / "scan_only.pdf"
    _make_scanned_image_pdf(scan_pdf)

    monkeypatch.setattr(
        "app.services.local_ocr.ocr_image_file",
        lambda _p: "Распознанный кириллический текст со скана документа о Telegram OSINT.",
    )

    res = await pdf_mod.extract_pdf_document(str(scan_pdf))
    assert res.is_scanned is True
    assert res.used_ocr is True
    assert res.ocr_pages == 1
    assert "Распознанный кириллический текст" in res.text


@pytest.mark.asyncio
async def test_scanned_pdf_falls_back_to_gemini_vision_when_local_ocr_thin(tmp_path: Path, monkeypatch):
    scan_pdf = tmp_path / "scan_vision.pdf"
    _make_scanned_image_pdf(scan_pdf)

    monkeypatch.setattr("app.services.local_ocr.ocr_image_file", lambda _p: "")

    async def _fake_gemini_ocr(image_path: str, *, settings, session=None) -> str:
        assert Path(image_path).is_file()
        return "Текст извлечённый через Gemini Vision со сложного скана и ссылка https://example.org/scan"

    monkeypatch.setattr(pdf_mod, "_gemini_ocr_page", _fake_gemini_ocr)

    res = await pdf_mod.extract_pdf_document(str(scan_pdf))
    assert res.is_scanned is True
    assert res.used_ocr is True
    assert res.ocr_pages == 1
    assert "Gemini Vision" in res.text
    assert "https://example.org/scan" in res.text


@pytest.mark.asyncio
async def test_scanned_pdf_reports_diagnostic_when_ocr_unavailable(tmp_path: Path, monkeypatch):
    scan_pdf = tmp_path / "scan_empty.pdf"
    _make_scanned_image_pdf(scan_pdf)

    monkeypatch.setattr("app.services.local_ocr.ocr_image_file", lambda _p: "")

    async def _empty_gemini(*_args, **_kwargs) -> str:
        return ""

    monkeypatch.setattr(pdf_mod, "_gemini_ocr_page", _empty_gemini)

    res = await pdf_mod.extract_pdf_document(str(scan_pdf))
    assert res.is_scanned is True
    assert res.used_ocr is False
    assert "не содержит текстового слоя" in res.text
    assert "empty_or_unreadable_scan" in res.warnings


@pytest.mark.asyncio
async def test_corrupt_and_encrypted_pdf_raise_clear_errors(tmp_path: Path):
    corrupt = tmp_path / "broken.pdf"
    corrupt.write_bytes(b"not a real pdf at all 12345")
    with pytest.raises(ValueError, match="Не удалось открыть PDF"):
        await pdf_mod.extract_pdf_document(str(corrupt))

    locked = tmp_path / "locked.pdf"
    _make_encrypted_pdf(locked)
    with pytest.raises(ValueError, match="паролем"):
        await pdf_mod.extract_pdf_document(str(locked))


def test_classify_url_detects_direct_pdf_urls():
    assert classify_url("https://example.com/docs/guide.pdf") == "pdf"
    assert classify_url("https://example.com/files/Report.PDF?download=1") == "pdf"
    assert classify_url("https://example.com/blog/how-to-read-pdfs") == "article"
    assert classify_url("https://youtu.be/dQw4w9WgXcQ") == "youtube"


@pytest.mark.asyncio
async def test_download_pdf_url_blocks_private_host_and_validates_pdf(tmp_path: Path, monkeypatch):
    with pytest.raises(article_mod.UnsafeURLError):
        await pdf_mod.download_pdf_url("http://127.0.0.1/secret.pdf", tmp_path)

    sample_pdf = tmp_path / "src.pdf"
    _make_multipage_cyrillic_pdf(sample_pdf)
    pdf_bytes = sample_pdf.read_bytes()

    async def _allow_url(u: str) -> str:
        return u

    monkeypatch.setattr(article_mod, "validate_public_http_url", _allow_url)

    class _FakeResp:
        def __init__(self, data: bytes, ct: str):
            self.status_code = 200
            self.headers = {"content-type": ct, "content-length": str(len(data))}
            self.url = "https://cdn.example.org/osint.pdf"
            self._data = data

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield self._data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _FakeClient:
        def __init__(self, data: bytes, ct: str):
            self._data = data
            self._ct = ct

        def stream(self, *_args, **_kwargs):
            return _FakeResp(self._data, self._ct)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(pdf_mod.httpx, "AsyncClient", lambda **_kw: _FakeClient(pdf_bytes, "application/pdf"))
    saved_path, hint = await pdf_mod.download_pdf_url("https://cdn.example.org/osint.pdf", tmp_path / "dl")
    assert Path(saved_path).is_file()
    assert hint == "osint.pdf"

    # Non-PDF payload is rejected and cleaned up
    monkeypatch.setattr(pdf_mod.httpx, "AsyncClient", lambda **_kw: _FakeClient(b"<html>hi</html>", "text/html"))
    with pytest.raises(ValueError, match="valid PDF"):
        await pdf_mod.download_pdf_url("https://cdn.example.org/fake.pdf", tmp_path / "dl2")


@pytest.mark.asyncio
async def test_extract_article_handles_pdf_content_type_seamlessly(tmp_path: Path, monkeypatch):
    sample_pdf = tmp_path / "arxiv_like.pdf"
    _make_multipage_cyrillic_pdf(sample_pdf)
    pdf_bytes = sample_pdf.read_bytes()

    async def _allow_url(u: str) -> str:
        return u

    monkeypatch.setattr(article_mod, "validate_public_http_url", _allow_url)

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "application/pdf", "content-length": str(len(pdf_bytes))}
        url = "https://arxiv.org/pdf/2607.12345"
        encoding = "utf-8"

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield pdf_bytes

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _FakeClient:
        def stream(self, *_args, **_kwargs):
            return _FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(article_mod.httpx, "AsyncClient", lambda **_kw: _FakeClient())

    title, text = await article_mod.extract_article("https://arxiv.org/pdf/2607.12345")
    assert title == "Практический справочник по OSINT в Telegram"
    assert "Инструменты для анализа каналов" in text
    assert "https://example.org/tool-1" in text


@pytest.mark.asyncio
async def test_groq_413_classified_as_payload_too_large_and_rotates_to_gemini(monkeypatch):
    reset_limits_for_tests()

    groq_413_msg = (
        "Error code: 413 - {'error': {'message': 'Request too large for model "
        "`openai/gpt-oss-120b` on tokens per minute (TPM): Limit 8000, Requested 9184', "
        "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
    )
    assert is_transient_rate_limit_error(groq_413_msg) is False

    # Verify GroqLLMClient converts 413 into PayloadTooLargeError
    client = GroqLLMClient.__new__(GroqLLMClient)
    client.model = "openai/gpt-oss-120b"
    client.provider = "groq"
    client.key_id = 1

    class _Err(Exception):
        status_code = 413

    class _FakeCompletions:
        async def create(self, **_kw):
            raise _Err(groq_413_msg)

    client.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    with pytest.raises(PayloadTooLargeError):
        await client.complete_json("sys", "usr")

    # Verify with_llm_fallback fails over from Groq 413 directly to Google without cooling down Groq key
    calls: list[str] = []
    marked: list[tuple[object, bool]] = []

    async def _fake_acquire(_session, provider: str):
        if provider == "groq":
            return [SimpleNamespace(id=1, api_key="gsk-test")]
        if provider == "google":
            return [SimpleNamespace(id=3, api_key="gem-test")]
        return []

    async def _fake_mark(_session, key_id, ok: bool, error=None):
        marked.append((key_id, ok))

    class _StubClient(LLMClient):
        def __init__(self, provider: str):
            self.provider = provider
            self.model = "stub"

        async def complete_json(self, system: str, user: str) -> LLMResult:
            calls.append(self.provider)
            if self.provider == "groq":
                raise PayloadTooLargeError(groq_413_msg)
            return LLMResult(
                card=KnowledgeCard(
                    title="OSINT в Telegram",
                    summary="Справочник по 25 инструментам OSINT.",
                    main_idea="Системный разбор поиска и архивирования в Telegram.",
                    digest="- Использовать TelegramDB\n- Проверять ID в Telegram Desktop",
                    usefulness_signals=UsefulnessSignals(
                        actionability=4,
                        novelty=3,
                        specificity=5,
                        reuse_likelihood=4,
                    ),
                    usefulness_score=7,
                    usefulness_reason="Практично",
                ),
                provider="google",
                model="gemini-flash-lite-latest",
            )

        async def complete_text(self, system: str, user: str) -> str:
            return "ok"

    monkeypatch.setattr("app.services.key_pool.acquire_keys", _fake_acquire)
    monkeypatch.setattr("app.services.key_pool.mark_key_result", _fake_mark)
    monkeypatch.setattr("app.ai._client_for", lambda prov, _k, _s, _kid: _StubClient(prov))

    res = await with_llm_fallback(object(), lambda c: c.complete_json("sys", "big"))
    assert res.provider == "google"
    assert calls == ["groq", "google"]
    assert not key_in_cooldown(1)
    assert (3, True) in marked
    assert (1, False) not in marked


@pytest.mark.asyncio
async def test_large_pdf_chunked_map_reduce_when_only_groq_available(monkeypatch):
    reset_limits_for_tests()

    # Build a 22,000-char multi-page Russian PDF text (well above GROQ_SAFE_INPUT_TOKENS)
    pages = [
        f"--- Страница {i} / 10 ---\n"
        + f"Раздел {i}: инструмент Tool{i} (https://example.org/tool-{i}) для анализа каналов Telegram.\n"
        + ("Подробное описание практических шагов и сценариев проверки данных в Telegram. " * 28)
        for i in range(1, 11)
    ]
    big_doc = "Документ: Большой справочник\nСтраниц: 10\n\n" + "\n\n".join(pages)
    assert estimate_tokens(big_doc) > 5000

    chunks = split_document_chunks(big_doc, max_chars=8500)
    assert len(chunks) >= 3
    assert all(len(c) <= 8500 for c in chunks)

    text_calls: list[int] = []
    json_prompt_lens: list[int] = []

    async def _groq_only_acquire(_session, provider: str):
        if provider == "groq":
            return [SimpleNamespace(id=1, api_key="gsk-test")]
        return []

    async def _noop_mark(*_args, **_kwargs):
        return None

    class _FakeGroq(LLMClient):
        provider = "groq"
        model = "openai/gpt-oss-120b"

        async def complete_text(self, system: str, user: str) -> str:
            text_calls.append(len(user))
            return "- Сводка фрагмента с ключевыми инструментами и ссылками."

        async def complete_json(self, system: str, user: str) -> LLMResult:
            json_prompt_lens.append(len(user))
            assert estimate_tokens(system + user) < 7000
            return LLMResult(
                card=KnowledgeCard(
                    title="Большой справочник по Telegram OSINT",
                    summary="Сводка по всем 10 страницам документа.",
                    main_idea="Полный разбор инструментов через поэтапную компрессию.",
                    digest="- Проверить каналы через Tool1\n- Выгрузить данные через Tool10",
                    usefulness_signals=UsefulnessSignals(
                        actionability=4,
                        novelty=3,
                        specificity=5,
                        reuse_likelihood=4,
                    ),
                    usefulness_score=7,
                    usefulness_reason="Высокая практичность",
                ),
                provider="groq",
                model=self.model,
            )

    monkeypatch.setattr("app.services.key_pool.acquire_keys", _groq_only_acquire)
    monkeypatch.setattr("app.services.key_pool.mark_key_result", _noop_mark)
    monkeypatch.setattr("app.ai._client_for", lambda _p, _k, _s, _kid: _FakeGroq())

    res = await analyze_material(
        big_doc,
        categories=["Инструменты"],
        tags=["telegram"],
        settings=Settings(ai_provider="groq", groq_api_key="gsk-test", google_ai_api_key=""),
        session=object(),
        personal_profile={},
    )
    assert res.card.title == "Большой справочник по Telegram OSINT"
    assert len(text_calls) >= 3
    assert len(json_prompt_lens) == 1
    assert json_prompt_lens[0] < len(big_doc)
