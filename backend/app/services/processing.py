from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai import analyze_material
from app.config import PROJECT_ROOT, get_settings
from app.logging_setup import get_logger
from app.models import (
    Material,
    MaterialTag,
    ProcessingStage,
    ProcessingStatus,
    TagStatus,
)
from app.pipelines import extract_text_for_material
from app.services.key_pool import any_provider_ready, get_app_settings
from app.services.resources import harvest_resources, merge_card_resources

log = get_logger(__name__)


async def find_duplicate(session: AsyncSession, content_hash: str | None) -> Material | None:
    if not content_hash:
        return None
    result = await session.execute(
        select(Material).where(
            Material.content_hash == content_hash,
            Material.processing_status.in_(
                [
                    ProcessingStatus.QUEUED.value,
                    ProcessingStatus.PROCESSING.value,
                    ProcessingStatus.PREVIEW.value,
                    ProcessingStatus.AWAITING_FULL.value,
                    ProcessingStatus.DONE.value,
                ]
            ),
        )
    )
    return result.scalars().first()


async def create_material(
    session: AsyncSession,
    *,
    source_type: str,
    source_url: str | None = None,
    file_path: str | None = None,
    note_text: str | None = None,
    content_hash: str | None = None,
    user_id: int | None = None,
    chat_id: int | None = None,
    title: str | None = None,
    entry_kind: str | None = None,
) -> Material:
    from app.models import EntryKind

    kind = (entry_kind or EntryKind.KNOWLEDGE.value).strip().lower()
    if kind not in {EntryKind.KNOWLEDGE.value, EntryKind.TASK.value, EntryKind.RULE.value}:
        kind = EntryKind.KNOWLEDGE.value

    dup = await find_duplicate(session, content_hash)
    if dup:
        return dup

    material = Material(
        id=str(uuid4()),
        title=title,
        source_type=source_type,
        entry_kind=kind,
        source_url=source_url,
        file_path=file_path,
        content_hash=content_hash,
        user_id=user_id,
        telegram_chat_id=chat_id,
        raw_text=note_text if source_type == "note" else None,
        processing_status=ProcessingStatus.QUEUED.value,
    )
    session.add(material)
    await session.commit()
    await session.refresh(material)
    return material


async def _taxonomy(session: AsyncSession) -> tuple[list[str], list[str], dict, dict]:
    from app.services.taxonomy import load_taxonomy

    tax = await load_taxonomy(session)
    return tax.categories, tax.approved_tags, tax.cat_by_key, tax.tag_by_key


async def _apply_card(session: AsyncSession, material: Material, card, cat_map, tag_map) -> None:
    from app.services.taxonomy import resolve_category, resolve_tag

    material.title = card.title
    material.summary = card.summary
    material.main_idea = card.main_idea
    if getattr(card, "digest", None):
        material.digest = card.digest
    elif not material.digest and card.summary:
        material.digest = card.summary
    material.key_ideas = card.key_ideas
    material.practical_takeaways = card.practical_takeaways

    harvest = harvest_resources(material.raw_text)
    card_links = card.links or []
    if card_links and hasattr(card_links[0], "model_dump"):
        card_links = [x.model_dump() for x in card_links]
    tools, links = merge_card_resources(card.tools or [], card_links, harvest)
    material.tools = tools
    material.technologies = card.technologies
    material.links = links
    blocks = getattr(card, "code_blocks", None) or []
    material.code_blocks = [
        b.model_dump() if hasattr(b, "model_dump") else b for b in blocks
    ]
    material.usefulness_score = card.usefulness_score
    material.usefulness_reason = card.usefulness_reason
    material.usefulness_updated_at = datetime.now(timezone.utc)

    cat = await resolve_category(session, card.category or "", cat_map, allow_create=False)
    if cat:
        material.category_id = cat.id
    else:
        material.category_id = None

    material.tags.clear()
    await session.flush()

    seen_tag_ids: set[int] = set()
    for tag_name in card.tags or []:
        tag = await resolve_tag(
            session,
            tag_name,
            tag_map,
            allow_create=True,
            auto_approve=False,
        )
        if not tag or tag.id in seen_tag_ids:
            continue
        seen_tag_ids.add(tag.id)
        session.add(
            MaterialTag(
                material_id=material.id,
                tag_id=tag.id,
                proposed=(tag.status == TagStatus.PENDING.value),
            )
        )


async def _refresh_search_vector(session: AsyncSession, material_id: str) -> None:
    if get_settings().is_sqlite:
        return
    await session.execute(
        text(
            """
            UPDATE materials
            SET search_vector =
              setweight(to_tsvector('russian', coalesce(title, '')), 'A') ||
              setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
              setweight(to_tsvector('russian', coalesce(summary, '')), 'A') ||
              setweight(to_tsvector('simple', coalesce(summary, '')), 'A') ||
              setweight(to_tsvector('russian', coalesce(main_idea, '')), 'B') ||
              setweight(to_tsvector('simple', coalesce(main_idea, '')), 'B') ||
              setweight(to_tsvector('russian', coalesce(raw_text, '')), 'C') ||
              setweight(to_tsvector('simple', coalesce(raw_text, '')), 'C')
            WHERE id = :id
            """
        ),
        {"id": material_id},
    )


def _decide_stage(
    source_type: str,
    duration: float | None,
    force_full: bool,
    settings,
    *,
    video_full: bool = True,
) -> str:
    if force_full or source_type not in {"youtube", "instagram", "tiktok", "video"}:
        return ProcessingStage.FULL.value
    if video_full:
        return ProcessingStage.FULL.value
    if duration is None:
        return ProcessingStage.PREVIEW.value
    if duration > settings.video_preview_threshold_min * 60:
        return ProcessingStage.PREVIEW.value
    return ProcessingStage.FULL.value


async def process_material(session: AsyncSession, material_id: str, *, force_full: bool = False) -> Material:
    settings = get_settings()
    result = await session.execute(
        select(Material)
        .options(selectinload(Material.tags).selectinload(MaterialTag.tag))
        .where(Material.id == material_id)
    )
    material = result.scalars().first()
    if not material:
        raise ValueError(f"Material {material_id} not found")

    material.processing_status = ProcessingStatus.PROCESSING.value
    material.error_message = None
    await session.commit()

    try:
        if not settings.ai_ready and not await any_provider_ready(session):
            raise RuntimeError("Нет API ключей — добавь Groq/Gemini в Настройках или .env")

        app_cfg = await get_app_settings(session)
        stage = _decide_stage(
            material.source_type,
            material.duration_seconds,
            force_full,
            settings,
            video_full=bool(app_cfg.get("video_full", True)),
        )

        storage = Path(settings.storage_path)
        if not storage.is_absolute():
            storage = PROJECT_ROOT / storage
        atlas_dir = str(storage / "materials" / material_id / "atlas")

        raw_text, title, dur, audio_sec, media_path = await extract_text_for_material(
            source_type=material.source_type,
            source_url=material.source_url,
            file_path=material.file_path,
            note_text=material.raw_text,
            stage=stage,
            settings=settings,
            atlas_dir=atlas_dir if material.source_type in {"youtube", "instagram", "tiktok", "video"} else None,
            session=session,
        )
        if dur is not None:
            material.duration_seconds = dur
        if media_path and Path(media_path).exists():
            material.file_path = media_path

        # If we requested preview but video turned out short, extractor already did full audio+OCR
        if (
            stage == ProcessingStage.PREVIEW.value
            and (material.duration_seconds or 0) <= settings.video_preview_threshold_min * 60
        ):
            stage = ProcessingStage.FULL.value

        material.raw_text = raw_text
        if title and not material.title:
            material.title = title
        material.processing_stage = stage
        material.usage_audio_seconds = (material.usage_audio_seconds or 0) + (audio_sec or 0)

        categories, tags, cat_map, tag_map = await _taxonomy(session)
        screen_resources = harvest_resources(raw_text).prompt_block()
        llm = await analyze_material(
            raw_text,
            categories=categories,
            tags=tags,
            preview=(stage == ProcessingStage.PREVIEW.value),
            settings=settings,
            session=session,
            personal_profile=app_cfg.get("personal_profile"),
            screen_resources=screen_resources,
            entry_kind=getattr(material, "entry_kind", None) or "knowledge",
        )
        material.usage_tokens_in = (material.usage_tokens_in or 0) + llm.tokens_in
        material.usage_tokens_out = (material.usage_tokens_out or 0) + llm.tokens_out
        meta = dict(material.usage_meta or {})
        meta.setdefault("runs", []).append(
            {
                "stage": stage,
                "provider": llm.provider,
                "model": llm.model,
                "tokens_in": llm.tokens_in,
                "tokens_out": llm.tokens_out,
                "audio_seconds": audio_sec,
                "score": llm.card.usefulness_score,
            }
        )
        material.usage_meta = meta

        await _apply_card(session, material, llm.card, cat_map, tag_map)

        if stage == ProcessingStage.PREVIEW.value:
            if llm.card.usefulness_score >= settings.video_full_score_threshold:
                await session.commit()
                return await process_material(session, material_id, force_full=True)
            material.processing_status = ProcessingStatus.AWAITING_FULL.value
        else:
            # Enrichment based on settings (reload material after nested commits)
            try:
                app_cfg = await get_app_settings(session)
                depth = app_cfg.get("default_depth") or "standard"
                result2 = await session.execute(
                    select(Material).where(Material.id == material_id)
                )
                material = result2.scalars().first() or material
                if depth == "detailed":
                    from app.services.enrichment import build_explanation, build_evaluation

                    await build_explanation(session, material)
                    await build_evaluation(session, material)
                if depth == "compact" and not (material.digest or "").strip():
                    from app.services.enrichment import rebuild_digest

                    await rebuild_digest(session, material)
                if (
                    app_cfg.get("auto_moments")
                    and material.source_type in {"youtube", "instagram", "tiktok", "video"}
                    and (getattr(material, "entry_kind", None) or "knowledge") == "knowledge"
                ):
                    from app.services.moments import build_key_moments

                    await build_key_moments(session, material, video_path=material.file_path)
            except Exception as e:
                log.warning("post_enrich_failed", material_id=material_id, error=str(e)[:300])
            material.processing_status = ProcessingStatus.DONE.value

        await _refresh_search_vector(session, material.id)
        await session.commit()
        await session.refresh(material)
        log.info(
            "material_processed",
            material_id=material.id,
            status=material.processing_status,
            stage=material.processing_stage,
            score=material.usefulness_score,
        )
        if stage == ProcessingStage.PREVIEW.value:
            # calibration aid: score bins near threshold
            log.info(
                "preview_score_bin",
                material_id=material.id,
                score=material.usefulness_score,
                near_threshold=4 <= (material.usefulness_score or 0) <= 6,
            )
        return material
    except Exception as e:
        log.exception(
            "material_failed",
            material_id=material_id,
            error_type=type(e).__name__,
            error=str(e)[:500],
        )
        try:
            await session.rollback()
        except Exception:
            pass
        # Fresh load — previous session state may be aborted after IntegrityError
        result = await session.execute(select(Material).where(Material.id == material_id))
        material = result.scalars().first()
        if material:
            material.processing_status = ProcessingStatus.FAILED.value
            material.error_message = f"{type(e).__name__}: {str(e)[:800]}"
            await session.commit()
        raise
