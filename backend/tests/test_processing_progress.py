"""Real processing progress steps (fetch/asr/card) — no timer fakes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_progress_enum_values():
    from app.models import ProcessingProgress

    assert ProcessingProgress.FETCH.value == "fetch"
    assert ProcessingProgress.ASR.value == "asr"
    assert ProcessingProgress.CARD.value == "card"
    assert ProcessingProgress.MOMENTS.value == "moments"


def test_ui_step_index_mapping():
    """Mirror MaterialProcessing.stepIndex rules."""

    def step_index(status: str, stage: str | None) -> int:
        if status == "queued":
            return 0
        if status not in {"processing", "failed"}:
            return 0
        s = (stage or "").lower()
        if s == "fetch":
            return 1
        if s == "asr":
            return 2
        if s in {"card", "preview", "full"}:
            return 3
        if s == "moments":
            return 4
        return 1

    assert step_index("queued", None) == 0
    assert step_index("processing", "fetch") == 1
    assert step_index("processing", "asr") == 2
    assert step_index("processing", "card") == 3
    assert step_index("processing", "moments") == 4
    assert step_index("processing", None) == 1


@pytest.mark.asyncio
async def test_extract_calls_progress_for_note():
    from app.pipelines import extract_text_for_material

    seen: list[str] = []

    async def on_progress(step: str):
        seen.append(step)

    text, title, *_ = await extract_text_for_material(
        source_type="note",
        source_url=None,
        file_path=None,
        note_text="hello note",
        stage="full",
        on_progress=on_progress,
    )
    assert text == "hello note"
    assert "fetch" in seen


def test_ytdlp_opts_uses_custom_logger_and_noprogress(tmp_path):
    from app.pipelines.youtube import _ytdlp_opts

    opts = _ytdlp_opts(tmp_path)
    assert opts["noprogress"] is True
    assert opts["quiet"] is True
    assert opts["logger"] is not None
    assert callable(getattr(opts["logger"], "error", None))
    assert callable(getattr(opts["logger"], "warning", None))


@pytest.mark.asyncio
async def test_first_run_card_failure_marks_failed_not_done(monkeypatch):
    from types import SimpleNamespace
    from app.models import ProcessingStatus
    from app.services import processing as proc

    mat = SimpleNamespace(
        id="m-first-fail",
        source_type="instagram",
        source_url="https://www.instagram.com/p/TestCode/",
        file_path=None,
        raw_text=None,
        title=None,
        summary=None,
        digest=None,
        main_idea=None,
        duration_seconds=None,
        usage_audio_seconds=0,
        processing_status=ProcessingStatus.QUEUED.value,
        processing_stage=None,
        error_message=None,
        entry_kind="knowledge",
        tags=[],
    )

    class _Scalars:
        def first(self):
            return mat

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def execute(self, _stmt):
            return _Result()

        async def commit(self):
            return None

        async def rollback(self):
            return None

    async def _fake_extract(**_kw):
        return "placeholder text", "instagram:TestCode", None, 0.0, None

    async def _fake_taxonomy(_session):
        return [], [], {}, {}

    async def _fake_analyze(*_a, **_kw):
        raise RuntimeError("SchemaValidationError: empty title")

    async def _fake_app_settings(_session):
        return {"video_full": True}

    async def _fake_ready(_session):
        return True

    monkeypatch.setattr(proc, "extract_text_for_material", _fake_extract)
    monkeypatch.setattr(proc, "_taxonomy", _fake_taxonomy)
    monkeypatch.setattr(proc, "analyze_material", _fake_analyze)
    monkeypatch.setattr(proc, "get_app_settings", _fake_app_settings)
    monkeypatch.setattr(proc, "any_provider_ready", _fake_ready)

    with pytest.raises(RuntimeError, match="SchemaValidationError"):
        await proc.process_material(_Session(), mat.id)

    assert mat.processing_status == ProcessingStatus.FAILED.value
    assert mat.processing_stage is None
    assert mat.error_message and "SchemaValidationError" in mat.error_message


@pytest.mark.asyncio
async def test_reprocess_failure_preserves_existing_ready_card(monkeypatch):
    from types import SimpleNamespace
    from app.models import ProcessingStage, ProcessingStatus
    from app.services import processing as proc

    mat = SimpleNamespace(
        id="m-reprocess-fail",
        source_type="instagram",
        source_url="https://www.instagram.com/p/TestCode2/",
        file_path=None,
        raw_text="old transcript",
        title="Готовая карточка",
        summary="Краткое содержание",
        digest="- Тезис 1",
        main_idea="Главная мысль",
        duration_seconds=30.0,
        usage_audio_seconds=30.0,
        processing_status=ProcessingStatus.QUEUED.value,
        processing_stage=ProcessingStage.FULL.value,
        error_message=None,
        entry_kind="knowledge",
        tags=[],
    )

    class _Scalars:
        def first(self):
            return mat

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def execute(self, _stmt):
            return _Result()

        async def commit(self):
            return None

        async def rollback(self):
            return None

    async def _fake_extract(**_kw):
        raise RuntimeError("transient network error")

    async def _fake_app_settings(_session):
        return {"video_full": True}

    async def _fake_ready(_session):
        return True

    monkeypatch.setattr(proc, "extract_text_for_material", _fake_extract)
    monkeypatch.setattr(proc, "get_app_settings", _fake_app_settings)
    monkeypatch.setattr(proc, "any_provider_ready", _fake_ready)

    with pytest.raises(RuntimeError, match="transient network error"):
        await proc.process_material(_Session(), mat.id)

    assert mat.processing_status == ProcessingStatus.DONE.value
    assert mat.processing_stage == ProcessingStage.FULL.value
    assert mat.error_message and "карточка сохранена" in mat.error_message

