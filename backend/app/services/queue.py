from __future__ import annotations

import asyncio

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_lite_tasks: set[asyncio.Task] = set()
_video_lock = asyncio.Lock()


async def _run_lite_job(material_id: str, force_full: bool = False) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.db import SessionLocal
    from app.models import Material, MaterialTag
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
                async with _video_lock:
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


async def enqueue_material(material_id: str, force_full: bool = False) -> None:
    settings = get_settings()
    if settings.is_lite:
        task = asyncio.create_task(_run_lite_job(material_id, force_full))
        _lite_tasks.add(task)
        task.add_done_callback(_lite_tasks.discard)
        log.info("lite_enqueued", material_id=material_id, force_full=force_full)
        return

    from app.workers.settings import get_arq_pool

    pool = await get_arq_pool()
    try:
        await pool.enqueue_job("process_material_job", material_id, force_full)
    finally:
        await pool.aclose()
