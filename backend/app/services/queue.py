from __future__ import annotations

import asyncio
import time

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_VIDEO_TYPES = frozenset({"youtube", "instagram", "tiktok", "video"})
_lite_tasks: set[asyncio.Task] = set()

_job_sem: asyncio.Semaphore | None = None
_job_sem_n: int | None = None
_video_sem: asyncio.Semaphore | None = None
_video_sem_n: int | None = None

VIDEO_SLOTS_KEY = "chronicum:video_slots"


def _reset_semaphores_for_tests() -> None:
    """Clear cached semaphores after settings monkeypatch in unit tests."""
    global _job_sem, _job_sem_n, _video_sem, _video_sem_n
    _job_sem = None
    _job_sem_n = None
    _video_sem = None
    _video_sem_n = None


def _job_semaphore() -> asyncio.Semaphore:
    global _job_sem, _job_sem_n
    n = max(1, int(get_settings().job_concurrency))
    if _job_sem is None or _job_sem_n != n:
        _job_sem = asyncio.Semaphore(n)
        _job_sem_n = n
    return _job_sem


def _video_semaphore() -> asyncio.Semaphore:
    global _video_sem, _video_sem_n
    n = max(1, int(get_settings().video_concurrency))
    if _video_sem is None or _video_sem_n != n:
        _video_sem = asyncio.Semaphore(n)
        _video_sem_n = n
    return _video_sem


def is_video_source(source_type: str | None) -> bool:
    return (source_type or "") in _VIDEO_TYPES


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
    from app.models import Material, MaterialTag
    from app.services.processing import process_material

    async with _job_semaphore():
        try:
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
        except Exception as e:
            log.exception("lite_job_failed", material_id=material_id, error=str(e)[:400])
            try:
                async with SessionLocal() as session:
                    from sqlalchemy import select

                    from app.bot.keyboards import material_keyboard_dict
                    from app.models import Material
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


async def enqueue_material(material_id: str, force_full: bool = False) -> None:
    settings = get_settings()
    if settings.is_lite:
        task = asyncio.create_task(_run_lite_job(material_id, force_full))
        _lite_tasks.add(task)
        task.add_done_callback(_lite_tasks.discard)
        log.info(
            "lite_enqueued",
            material_id=material_id,
            force_full=force_full,
            job_concurrency=settings.job_concurrency,
            video_concurrency=settings.video_concurrency,
            inflight=len(_lite_tasks),
        )
        return

    from app.workers.settings import get_arq_pool

    pool = await get_arq_pool()
    try:
        await pool.enqueue_job("process_material_job", material_id, force_full)
    finally:
        await pool.aclose()
