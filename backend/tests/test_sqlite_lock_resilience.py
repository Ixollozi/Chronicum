"""Regression tests for SQLite write-lock resilience in key_pool and processing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base, configure_sqlite_engine
from app.models import ApiKey, Material, ProcessingStatus
from app.services import key_pool as kp
from app.services import processing as proc


async def _make_file_db(tmp_path: Path, name: str):
    db_path = tmp_path / name
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    configure_sqlite_engine(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


@pytest.mark.asyncio
async def test_mark_key_result_does_not_hold_sqlite_writer_lock_after_select(tmp_path: Path):
    kp.reset_key_touch_cache()
    engine, factory = await _make_file_db(tmp_path, "lock_check.db")
    try:
        async with factory() as s:
            await kp.add_keys(s, "groq", "gsk_lock_test_key_1234567890", label="lock")
            m = Material(source_type="instagram", title="before", processing_status="processing")
            s.add(m)
            await s.commit()
            mid = m.id

        async with factory() as session_a:
            # Start an implicit read transaction (the exact state before extract_text_for_material)
            keys = await kp.acquire_keys(session_a, "groq")
            assert session_a.in_transaction()
            assert not session_a.dirty

            # Must commit in a sibling session, NOT flush uncommitted writes on session_a
            await kp.mark_key_result(session_a, keys[0].id, ok=True)

            # While session_a remains open (simulating 60s build_screen_atlas),
            # a second writer with a tiny 50ms busy_timeout MUST succeed immediately.
            async with factory() as session_b:
                await session_b.execute(text("PRAGMA busy_timeout=50"))
                row = await session_b.get(Material, mid)
                row.title = "updated concurrently"
                await session_b.commit()

        async with factory() as verify:
            row = await verify.get(Material, mid)
            assert row.title == "updated concurrently"
            key_row = (await verify.execute(select(ApiKey))).scalars().first()
            assert key_row.last_ok_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mark_key_result_throttles_ok_touches_and_resets_on_error(
    tmp_path: Path, monkeypatch
):
    kp.reset_key_touch_cache()
    engine, factory = await _make_file_db(tmp_path, "throttle_check.db")
    try:
        async with factory() as s:
            await kp.add_keys(s, "groq", "gsk_throttle_test_1234567890", label="thr")
            keys = await kp.acquire_keys(s, "groq")
            kid = keys[0].id

        calls: list[str] = []
        orig_commit = kp._commit_isolated

        async def _spy(session, fn):
            calls.append("write")
            return await orig_commit(session, fn)

        monkeypatch.setattr(kp, "_commit_isolated", _spy)

        async with factory() as s:
            await kp.mark_key_result(s, kid, ok=True)
            await kp.mark_key_result(s, kid, ok=True)
            assert len(calls) == 1  # second ok=True within 60s throttled

            await kp.mark_key_result(s, kid, ok=False, error="boom")
            assert len(calls) == 2  # error always persisted & clears throttle

            await kp.mark_key_result(s, kid, ok=True)
            assert len(calls) == 3  # ok after error immediately clears last_error

        async with factory() as verify:
            key_row = await verify.get(ApiKey, kid)
            assert key_row.last_error is None
            assert key_row.last_ok_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mark_key_result_swallows_sqlite_locked_error(tmp_path: Path, monkeypatch):
    kp.reset_key_touch_cache()
    engine, factory = await _make_file_db(tmp_path, "swallow_lock.db")
    try:
        async def _always_locked(_session, _fn):
            raise OperationalError("UPDATE api_keys", {}, Exception("database is locked"))

        monkeypatch.setattr(kp, "_commit_isolated", _always_locked)

        async with factory() as s:
            # Must NOT raise and crash the material pipeline
            await kp.mark_key_result(s, 1, ok=True)
            await kp.mark_key_result(s, 1, ok=False, error="some api err")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_process_material_releases_lock_before_enrichment_and_embedding(
    tmp_path: Path, monkeypatch
):
    kp.reset_key_touch_cache()
    engine, factory = await _make_file_db(tmp_path, "proc_lock.db")
    try:
        async with factory() as s:
            m = Material(
                source_type="note",
                title="raw note",
                raw_text="Some useful engineering text about SQLite WAL and FastAPI.",
                processing_status=ProcessingStatus.QUEUED.value,
            )
            s.add(m)
            await s.commit()
            mid = m.id

        async def _fake_extract(**kwargs):
            on_progress = kwargs.get("on_progress")
            if on_progress:
                await on_progress("asr")
            return (
                "Подробный разбор архитектуры SQLite WAL с конкретными шагами и примерами.",
                "SQLite WAL разбор",
                None,
                0.0,
                None,
            )

        fake_card = SimpleNamespace(
            title="SQLite WAL в продакшене",
            summary="Краткий разбор коротких транзакций в SQLite WAL.",
            main_idea="Не держи write-lock поверх сетевых вызовов.",
            digest="- Короткие транзакции\n- Коммит до LLM",
            key_ideas=["Коммит до LLM"],
            practical_takeaways=["Коммить карточку до enrichment"],
            tools=["SQLite"],
            technologies=["SQLite"],
            links=[],
            code_blocks=[],
            usefulness_score=8,
            usefulness_reason="Практическая ценность",
            category=None,
            tags=["sqlite"],
        )
        fake_llm = SimpleNamespace(
            card=fake_card,
            provider="groq",
            model="test-model",
            tokens_in=100,
            tokens_out=50,
        )

        async def _fake_analyze(*args, **kwargs):
            return fake_llm

        enrichment_saw_committed_card = False
        embedding_session_clean = False
        verify_links_unlocked = False

        async def _verify_links_check(links):
            nonlocal verify_links_unlocked
            async with factory() as other:
                await other.execute(text("PRAGMA busy_timeout=50"))
                other.add(Material(source_type="note", title="during-verify", processing_status="queued"))
                await other.commit()
            verify_links_unlocked = True
            return links

        async def _fake_explanation(session, material):
            nonlocal enrichment_saw_committed_card
            # Verify another connection can read committed card AND write immediately (no lock held!)
            async with factory() as other:
                await other.execute(text("PRAGMA busy_timeout=50"))
                m_other = await other.get(Material, mid)
                enrichment_saw_committed_card = (
                    m_other is not None and m_other.title == "SQLite WAL в продакшене"
                )
                other_m = Material(source_type="note", title="concurrent", processing_status="queued")
                other.add(other_m)
                await other.commit()
            material.detailed_explanation = "## Суть\nКороткие транзакции."
            await session.commit()
            return material

        async def _fake_embedding(_text, *, settings=None, session=None):
            nonlocal embedding_session_clean
            embedding_session_clean = bool(
                session is not None
                and not session.dirty
                and not session.new
                and not session.deleted
            )
            return {"model": "test", "dim": 2, "vec": [0.1, 0.2]}

        monkeypatch.setattr(proc, "any_provider_ready", lambda _s: _async_true())
        monkeypatch.setattr(
            proc,
            "get_app_settings",
            lambda _s: _async_val({"default_depth": "detailed", "auto_moments": False, "video_full": True}),
        )
        monkeypatch.setattr(proc, "extract_text_for_material", _fake_extract)
        monkeypatch.setattr(proc, "analyze_material", _fake_analyze)
        monkeypatch.setattr("app.services.enrichment.build_explanation", _fake_explanation)
        monkeypatch.setattr("app.services.enrichment.build_evaluation", _noop_async_two)
        monkeypatch.setattr("app.search.embeddings.compute_embedding", _fake_embedding)
        monkeypatch.setattr("app.services.verify.verify_links", _verify_links_check)

        async with factory() as s:
            out = await proc.process_material(s, mid)
            assert out.processing_status == ProcessingStatus.DONE.value
            assert out.embedding == {"model": "test", "dim": 2, "vec": [0.1, 0.2]}

        assert verify_links_unlocked is True
        assert enrichment_saw_committed_card is True
        assert embedding_session_clean is True
    finally:
        await engine.dispose()


async def _async_true():
    return True


async def _async_val(val):
    return val


async def _noop_async_two(_s, m):
    return m


async def _passthrough_links(links):
    return links
