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
