from __future__ import annotations

import redis.asyncio as redis
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot.keyboards import (
    material_keyboard_dict,
    pending_tags_keyboard_dict,
    proposed_tags_from_material,
)
from app.config import get_settings
from app.db import SessionLocal
from app.logging_setup import get_logger, setup_logging
from app.models import Material, MaterialTag
from app.services.processing import process_material

log = get_logger(__name__)

VIDEO_LOCK_KEY = "chronicum:video_lock"


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_arq_pool():
    return await create_pool(_redis_settings())


async def notify_user(
    chat_id: int | None,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> None:
    if not chat_id:
        return
    settings = get_settings()
    if not settings.telegram_ready:
        log.warning("notify_skipped_no_telegram_token")
        return
    import httpx

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text[:4000]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            log.warning("notify_failed", status=resp.status_code, body=resp.text[:300])


def format_card(material) -> str:
    tags = []
    for mt in getattr(material, "tags", []) or []:
        name = mt.tag.name if mt.tag else "?"
        if mt.proposed:
            name = f"{name}?"
        tags.append(name)
    status = material.processing_status
    emoji = "✅"
    if status == "awaiting_full":
        emoji = "⏸"
    elif status == "failed":
        emoji = "❌"
    lines = [
        f"{emoji} {material.title or 'Без названия'}",
        f"id: `{material.id}`",
        f"status: {status} | stage: {material.processing_stage or '-'}",
        f"score: {material.usefulness_score}/10 — {material.usefulness_reason or ''}",
        f"category: {material.category.name if getattr(material, 'category', None) else '—'}",
        f"tags: {', '.join(tags) if tags else '—'}",
        "",
        material.summary or "",
        "",
        f"💡 {material.main_idea or ''}",
    ]
    if status == "awaiting_full":
        lines.append("")
        lines.append("Длинное видео: полный прогон не запущен — жми кнопку ниже.")
    return "\n".join(lines)


def card_reply_markup(material) -> dict | None:
    status = material.processing_status
    kb = material_keyboard_dict(
        material.id,
        awaiting_full=(status == "awaiting_full"),
        failed=(status == "failed"),
    )
    tag_kb = pending_tags_keyboard_dict(proposed_tags_from_material(material))
    if not tag_kb:
        return kb
    rows = list(kb.get("inline_keyboard") or [])
    rows.extend(tag_kb.get("inline_keyboard") or [])
    return {"inline_keyboard": rows}


async def notify_material_card(material) -> None:
    await notify_user(
        material.telegram_chat_id,
        format_card(material),
        reply_markup=card_reply_markup(material),
    )


async def process_material_job(ctx, material_id: str, force_full: bool = False) -> None:
    settings = get_settings()
    job_id = getattr(ctx, "job_id", None) or (ctx.get("job_id") if isinstance(ctx, dict) else None)
    log.info("job_start", material_id=material_id, job_id=job_id, force_full=force_full)

    r = redis.from_url(settings.redis_url)
    lock_acquired = False
    try:
        async with SessionLocal() as session:
            material = (
                await session.execute(select(Material).where(Material.id == material_id))
            ).scalars().first()
            if not material:
                return

            if material.source_type in {"youtube", "instagram", "video"}:
                lock_acquired = bool(await r.set(VIDEO_LOCK_KEY, material_id, nx=True, ex=3600))
                if not lock_acquired:
                    # Another video job holds the lock — requeue shortly
                    pool = await get_arq_pool()
                    try:
                        await pool.enqueue_job(
                            "process_material_job",
                            material_id,
                            force_full,
                            _defer_by=30,
                        )
                    finally:
                        await pool.aclose()
                    log.info("video_deferred_concurrency", material_id=material_id)
                    return

            try:
                await process_material(session, material_id, force_full=force_full)
                material = (
                    await session.execute(
                        select(Material)
                        .options(
                            selectinload(Material.category),
                            selectinload(Material.tags).selectinload(MaterialTag.tag),
                        )
                        .where(Material.id == material_id)
                    )
                ).scalars().first()
                if material:
                    await notify_material_card(material)
            except Exception as e:
                material = (
                    await session.execute(select(Material).where(Material.id == material_id))
                ).scalars().first()
                chat_id = material.telegram_chat_id if material else None
                err = (material.error_message if material else None) or str(e)
                await notify_user(
                    chat_id,
                    f"❌ Обработка failed\nid: `{material_id}`\n{err}",
                    reply_markup=material_keyboard_dict(material_id, failed=True),
                )
    finally:
        if lock_acquired:
            await r.delete(VIDEO_LOCK_KEY)
        await r.aclose()


class WorkerSettings:
    functions = [process_material_job]
    redis_settings = _redis_settings()
    max_jobs = get_settings().arq_max_jobs
    job_timeout = 60 * 60

    @staticmethod
    async def on_startup(ctx):
        setup_logging(get_settings().log_level)
        log.info("worker_started", max_jobs=get_settings().arq_max_jobs)
