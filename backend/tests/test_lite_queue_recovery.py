from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Material, ProcessingStage, ProcessingStatus, SourceType
from app.services import queue as queue_mod


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_recover_lite_queue_resets_processing_and_reenqueues_pending(monkeypatch):
    engine, factory = await _make_db()
    try:
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        async with factory() as session:
            session.add_all(
                [
                    Material(
                        id="m-processing",
                        source_type=SourceType.YOUTUBE.value,
                        processing_status=ProcessingStatus.PROCESSING.value,
                        processing_stage=ProcessingStage.FULL.value,
                        error_message="old transient error",
                        created_at=t0,
                    ),
                    Material(
                        id="m-queued",
                        source_type=SourceType.NOTE.value,
                        processing_status=ProcessingStatus.QUEUED.value,
                        processing_stage=ProcessingStage.PREVIEW.value,
                        created_at=t0 + timedelta(seconds=10),
                    ),
                    Material(
                        id="m-done",
                        source_type=SourceType.NOTE.value,
                        processing_status=ProcessingStatus.DONE.value,
                        created_at=t0 + timedelta(seconds=20),
                    ),
                    Material(
                        id="m-failed",
                        source_type=SourceType.NOTE.value,
                        processing_status=ProcessingStatus.FAILED.value,
                        created_at=t0 + timedelta(seconds=30),
                    ),
                ]
            )
            await session.commit()

        enqueued: list[tuple[str, bool]] = []

        async def fake_enqueue(mid: str, force_full: bool = False):
            enqueued.append((mid, force_full))

        monkeypatch.setattr(queue_mod, "enqueue_material", fake_enqueue)

        recovered = await queue_mod.recover_lite_queue(session_factory=factory)

        assert recovered == ["m-processing", "m-queued"]
        assert enqueued == [("m-processing", True), ("m-queued", False)]

        async with factory() as session:
            m_proc = (
                await session.execute(select(Material).where(Material.id == "m-processing"))
            ).scalars().first()
            assert m_proc is not None
            assert m_proc.processing_status == ProcessingStatus.QUEUED.value
            assert m_proc.error_message is None
    finally:
        await engine.dispose()


async def test_enqueue_material_deduplicates_in_flight_jobs(monkeypatch):
    queue_mod._in_flight.clear()
    started = asyncio.Event()
    release = asyncio.Event()
    run_calls: list[str] = []

    async def fake_run(mid: str, force_full: bool = False):
        run_calls.append(mid)
        started.set()
        await release.wait()

    monkeypatch.setattr(queue_mod, "_run_lite_job", fake_run)
    monkeypatch.setattr(
        queue_mod,
        "get_settings",
        lambda: SimpleNamespace(is_lite=True, video_concurrency=1),
    )

    await queue_mod.enqueue_material("dup-1")
    await started.wait()
    # Second enqueue while first is still running must be deduplicated
    await queue_mod.enqueue_material("dup-1")

    assert queue_mod.active_lite_jobs() == ["dup-1"]
    assert run_calls == ["dup-1"]

    release.set()
    await queue_mod.shutdown_lite_queue(timeout=2.0)
    assert queue_mod.active_lite_jobs() == []


async def test_shutdown_lite_queue_cancels_hanging_tasks(monkeypatch):
    queue_mod._in_flight.clear()
    started = asyncio.Event()
    cancelled = {"flag": False}

    async def hanging_job(mid: str, force_full: bool = False):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled["flag"] = True
            raise

    monkeypatch.setattr(queue_mod, "_run_lite_job", hanging_job)
    monkeypatch.setattr(
        queue_mod,
        "get_settings",
        lambda: SimpleNamespace(is_lite=True, video_concurrency=1),
    )

    await queue_mod.enqueue_material("hang-1")
    await started.wait()

    await queue_mod.shutdown_lite_queue(timeout=0.05)

    assert cancelled["flag"] is True
    assert queue_mod.active_lite_jobs() == []


async def test_video_semaphore_respects_video_concurrency(monkeypatch):
    monkeypatch.setattr(
        queue_mod,
        "get_settings",
        lambda: SimpleNamespace(is_lite=True, video_concurrency=2),
    )
    sem = queue_mod._get_video_semaphore()
    assert sem._value == 2

    monkeypatch.setattr(
        queue_mod,
        "get_settings",
        lambda: SimpleNamespace(is_lite=True, video_concurrency=1),
    )
    sem2 = queue_mod._get_video_semaphore()
    assert sem2._value == 1


async def test_reap_stale_lite_jobs_reclaims_orphan_and_fails_hung(monkeypatch):
    queue_mod._in_flight.clear()
    engine, factory = await _make_db()
    hung_started = asyncio.Event()
    hung_cancelled = {"flag": False}

    async def hung_coro():
        hung_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            hung_cancelled["flag"] = True
            raise

    hung_task = asyncio.create_task(hung_coro())
    await hung_started.wait()
    queue_mod._in_flight["m-hung"] = hung_task

    try:
        old = datetime.now(timezone.utc) - timedelta(seconds=1200)
        mid_old = datetime.now(timezone.utc) - timedelta(seconds=90)
        async with factory() as session:
            session.add_all(
                [
                    Material(
                        id="m-orphan",
                        source_type=SourceType.NOTE.value,
                        processing_status=ProcessingStatus.PROCESSING.value,
                        processing_stage=ProcessingStage.FULL.value,
                        created_at=mid_old,
                        updated_at=mid_old,
                    ),
                    Material(
                        id="m-hung",
                        source_type=SourceType.NOTE.value,
                        processing_status=ProcessingStatus.PROCESSING.value,
                        created_at=old,
                        updated_at=old,
                    ),
                    Material(
                        id="m-fresh",
                        source_type=SourceType.NOTE.value,
                        processing_status=ProcessingStatus.PROCESSING.value,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    ),
                ]
            )
            await session.commit()

        enqueued: list[tuple[str, bool]] = []

        async def fake_enqueue(mid: str, force_full: bool = False):
            enqueued.append((mid, force_full))

        monkeypatch.setattr(queue_mod, "enqueue_material", fake_enqueue)

        affected = await queue_mod.reap_stale_lite_jobs(
            session_factory=factory,
            orphan_after_sec=30.0,
            stale_after_sec=600.0,
        )

        assert "m-orphan" in affected
        assert "m-hung" in affected
        assert "m-fresh" not in affected
        assert enqueued == [("m-orphan", True)]

        await asyncio.gather(hung_task, return_exceptions=True)
        assert hung_cancelled["flag"] is True

        async with factory() as session:
            m_orphan = (
                await session.execute(select(Material).where(Material.id == "m-orphan"))
            ).scalars().first()
            m_hung = (
                await session.execute(select(Material).where(Material.id == "m-hung"))
            ).scalars().first()
            assert m_orphan is not None
            assert m_orphan.processing_status == ProcessingStatus.QUEUED.value
            assert m_hung is not None
            assert m_hung.processing_status == ProcessingStatus.FAILED.value
            assert "таймаут" in (m_hung.error_message or "").lower()
    finally:
        queue_mod._in_flight.clear()
        await engine.dispose()


async def test_run_lite_job_marks_failed_on_timeout(monkeypatch):
    import app.db as db_mod
    import app.services.processing as proc_mod

    engine, factory = await _make_db()
    try:
        async with factory() as session:
            session.add(
                Material(
                    id="m-timeout",
                    source_type=SourceType.NOTE.value,
                    processing_status=ProcessingStatus.PROCESSING.value,
                )
            )
            await session.commit()

        async def slow_process(session, material_id, force_full=False):
            await asyncio.sleep(5)

        monkeypatch.setattr(db_mod, "SessionLocal", factory)
        monkeypatch.setattr(proc_mod, "process_material", slow_process)
        monkeypatch.setattr(queue_mod, "LITE_TEXT_JOB_TIMEOUT_SEC", 0.05)
        monkeypatch.setattr(
            queue_mod,
            "get_settings",
            lambda: SimpleNamespace(is_lite=True, job_concurrency=2, video_concurrency=1),
        )
        queue_mod._reset_semaphores_for_tests()

        await queue_mod._run_lite_job("m-timeout")

        async with factory() as session:
            m = (
                await session.execute(select(Material).where(Material.id == "m-timeout"))
            ).scalars().first()
            assert m is not None
            assert m.processing_status == ProcessingStatus.FAILED.value
            assert "таймаут" in (m.error_message or "").lower()
    finally:
        queue_mod._reset_semaphores_for_tests()
        await engine.dispose()
