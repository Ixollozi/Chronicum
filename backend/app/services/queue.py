from __future__ import annotations

import asyncio
import time
from weakref import WeakKeyDictionary

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_VIDEO_TYPES = frozenset({"youtube", "instagram", "tiktok", "video"})
_in_flight: dict[str, asyncio.Task] = {}
_lite_tasks: set[asyncio.Task] = set()

_job_semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = (
    WeakKeyDictionary()
)
_video_semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = (
    WeakKeyDictionary()
)

VIDEO_SLOTS_KEY = "chronicum:video_slots"


def _reset_semaphores_for_tests() -> None:
    """Clear cached semaphores after settings monkeypatch in unit tests."""
    _job_semaphores.clear()
    _video_semaphores.clear()


def _job_semaphore() -> asyncio.Semaphore:
    n = max(1, int(get_settings().job_concurrency or 1))
    loop = asyncio.get_running_loop()
    cached = _job_semaphores.get(loop)
    if cached is None or cached[0] != n:
        sem = asyncio.Semaphore(n)
        _job_semaphores[loop] = (n, sem)
        return sem
    return cached[1]


def _video_semaphore() -> asyncio.Semaphore:
    n = max(1, int(get_settings().video_concurrency or 1))
    loop = asyncio.get_running_loop()
    cached = _video_semaphores.get(loop)
    if cached is None or cached[0] != n:
        sem = asyncio.Semaphore(n)
        _video_semaphores[loop] = (n, sem)
        return sem
    return cached[1]


# Backwards-compatible alias used by hardening tests
_get_video_semaphore = _video_semaphore


def is_video_source(source_type: str | None) -> bool:
    return (source_type or "") in _VIDEO_TYPES


def active_lite_jobs() -> list[str]:
    return [mid for mid, task in _in_flight.items() if not task.done()]


async def try_acquire_video_slot(redis_client, material_id: str, *, max_slots: int, ttl: int = 3600) -> bool:
    """Atomic Redis slot acquire for ARQ workers (zset + expiry)."""
    now = time.time()
    script = """
    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
    local c = redis.call('ZCARD', KEYS[1])
    if c >= tonumber(ARGV[2]) then
      return 0
    end
    redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
    return 1
    """
    ok = await redis_client.eval(
        script,
        1,
        VIDEO_SLOTS_KEY,
        str(now),
        str(max(1, max_slots)),
        str(now + ttl),
        material_id,
    )
    return bool(int(ok or 0))


async def release_video_slot(redis_client, material_id: str) -> None:
    await redis_client.zrem(VIDEO_SLOTS_KEY, material_id)


async def _run_lite_job(material_id: str, force_full: bool = False) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.db import SessionLocal
    from app.models import Material, MaterialTag, ProcessingStatus
    from app.services.processing import process_material

    try:
        async with _job_semaphore():
            async with SessionLocal() as session:
                material = (
                    await session.execute(select(Material).where(Material.id == material_id))
                ).scalars().first()
                if not material:
                    return

                async def _process():
                    await process_material(session, material_id, force_full=force_full)
                    return (
                        await session.execute(
                            select(Material)
                            .options(
                                selectinload(Material.category),
                                selectinload(Material.tags).selectinload(MaterialTag.tag),
                            )
                            .where(Material.id == material_id)
                        )
                    ).scalars().first()

                if is_video_source(material.source_type):
                    async with _video_semaphore():
                        material = await _process()
                else:
                    material = await _process()

                if material:
                    from app.workers.settings import notify_material_card

                    await notify_material_card(material)
    except asyncio.CancelledError:
        log.info("lite_job_cancelled", material_id=material_id)
        try:
            async with SessionLocal() as session:
                material = (
                    await session.execute(select(Material).where(Material.id == material_id))
                ).scalars().first()
                if material and material.processing_status == ProcessingStatus.PROCESSING.value:
                    material.processing_status = ProcessingStatus.QUEUED.value
                    material.processing_stage = None
                    await session.commit()
        except Exception:
            pass
        raise
    except Exception as e:
        log.exception("lite_job_failed", material_id=material_id, error=str(e)[:400])
        try:
            async with SessionLocal() as session:
                from app.bot.keyboards import material_keyboard_dict
                from app.workers.settings import notify_user

                material = (
                    await session.execute(select(Material).where(Material.id == material_id))
                ).scalars().first()
                if material:
                    await notify_user(
                        material.telegram_chat_id,
                        f"❌ failed `{material_id}`\n{material.error_message or e}",
                        reply_markup=material_keyboard_dict(material_id, failed=True),
                    )
        except Exception:
            pass


async def reclaim_stale_jobs(session) -> list[str]:
    """Reset stuck `processing` → `queued` after process restart; return all queued ids."""
    from sqlalchemy import select

    from app.models import Material, ProcessingStatus

    stuck = (
        await session.execute(
            select(Material).where(Material.processing_status == ProcessingStatus.PROCESSING.value)
        )
    ).scalars().all()
    for m in stuck:
        m.processing_status = ProcessingStatus.QUEUED.value
        m.processing_stage = None
        m.error_message = None
    if stuck:
        await session.commit()
        log.info("jobs_reclaimed", count=len(stuck), ids=[m.id for m in stuck][:20])

    queued = (
        await session.execute(
            select(Material.id).where(Material.processing_status == ProcessingStatus.QUEUED.value)
        )
    ).scalars().all()
    return list(queued)


async def reclaim_and_enqueue() -> list[str]:
    """Startup: reclaim stuck jobs and enqueue every queued material (lite/full)."""
    from app.db import SessionLocal

    async with SessionLocal() as session:
        ids = await reclaim_stale_jobs(session)
    for mid in ids:
        await enqueue_material(mid)
    if ids:
        log.info("jobs_requeued_after_startup", count=len(ids))
    return ids


def _register_lite_task(material_id: str, task: asyncio.Task) -> None:
    _in_flight[material_id] = task
    _lite_tasks.add(task)

    def _cleanup(done_task: asyncio.Task) -> None:
        _lite_tasks.discard(done_task)
        if _in_flight.get(material_id) is done_task:
            _in_flight.pop(material_id, None)

    task.add_done_callback(_cleanup)


async def enqueue_material(material_id: str, force_full: bool = False) -> None:
    settings = get_settings()
    if settings.is_lite:
        existing = _in_flight.get(material_id)
        if existing is not None and not existing.done():
            log.info("lite_enqueue_deduped", material_id=material_id)
            return
        task = asyncio.create_task(_run_lite_job(material_id, force_full))
        _register_lite_task(material_id, task)
        log.info(
            "lite_enqueued",
            material_id=material_id,
            force_full=force_full,
            job_concurrency=getattr(settings, "job_concurrency", 6),
            video_concurrency=getattr(settings, "video_concurrency", 3),
            inflight=len(_lite_tasks),
        )
        return

    from app.workers.settings import get_arq_pool

    pool = await get_arq_pool()
    try:
        await pool.enqueue_job("process_material_job", material_id, force_full)
    finally:
        await pool.aclose()


async def recover_lite_queue(session_factory=None) -> list[str]:
    """
    Re-enqueue materials left in `queued` or `processing` after a Lite-mode restart.
    Resets interrupted `processing` rows back to `queued` before scheduling.
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Material, ProcessingStage, ProcessingStatus

    factory = session_factory or SessionLocal
    pending: list[tuple[str, bool]] = []
    async with factory() as session:
        rows = (
            await session.execute(
                select(Material)
                .where(
                    Material.processing_status.in_(
                        [ProcessingStatus.QUEUED.value, ProcessingStatus.PROCESSING.value]
                    )
                )
                .order_by(Material.created_at.asc())
            )
        ).scalars().all()
        changed = False
        for m in rows:
            force = m.processing_stage == ProcessingStage.FULL.value
            if m.processing_status == ProcessingStatus.PROCESSING.value:
                m.processing_status = ProcessingStatus.QUEUED.value
                m.processing_stage = None
                m.error_message = None
                changed = True
            pending.append((m.id, force))
        if changed:
            await session.commit()

    recovered: list[str] = []
    for mid, force_full in pending:
        await enqueue_material(mid, force_full=force_full)
        recovered.append(mid)

    if recovered:
        log.info("lite_queue_recovered", count=len(recovered), material_ids=recovered)
    return recovered


async def shutdown_lite_queue(timeout: float = 5.0) -> None:
    """Wait briefly for active Lite tasks on shutdown, then cancel any remaining ones."""
    tasks = [t for t in _in_flight.values() if not t.done()]
    if not tasks:
        return
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    log.info("lite_queue_shutdown", completed=len(done), cancelled=len(pending))
