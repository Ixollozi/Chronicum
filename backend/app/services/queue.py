from __future__ import annotations

import asyncio
from weakref import WeakKeyDictionary

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_in_flight: dict[str, asyncio.Task] = {}
_video_semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = (
    WeakKeyDictionary()
)


def _get_video_semaphore() -> asyncio.Semaphore:
    limit = max(1, int(get_settings().video_concurrency or 1))
    loop = asyncio.get_running_loop()
    cached = _video_semaphores.get(loop)
    if cached is None or cached[0] != limit:
        sem = asyncio.Semaphore(limit)
        _video_semaphores[loop] = (limit, sem)
        return sem
    return cached[1]


def active_lite_jobs() -> list[str]:
    return [mid for mid, task in _in_flight.items() if not task.done()]


async def _run_lite_job(material_id: str, force_full: bool = False) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.db import SessionLocal
    from app.models import Material, MaterialTag, ProcessingStatus
    from app.services.processing import process_material

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

            if material.source_type in {"youtube", "instagram", "tiktok", "video"}:
                async with _get_video_semaphore():
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
                    await session.commit()
        except Exception:
            pass
        raise
    except Exception as e:
        log.exception("lite_job_failed", material_id=material_id, error=str(e)[:400])
        try:
            async with SessionLocal() as session:
                from sqlalchemy import select
                from app.models import Material
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


def _register_lite_task(material_id: str, task: asyncio.Task) -> None:
    _in_flight[material_id] = task

    def _cleanup(done_task: asyncio.Task) -> None:
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
        log.info("lite_enqueued", material_id=material_id, force_full=force_full)
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
            if m.processing_status == ProcessingStatus.PROCESSING.value:
                m.processing_status = ProcessingStatus.QUEUED.value
                m.error_message = None
                changed = True
            pending.append((m.id, m.processing_stage == ProcessingStage.FULL.value))
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
