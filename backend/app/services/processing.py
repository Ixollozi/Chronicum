from __future__ import annotations

import re
from dataclasses import dataclass
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
    ProcessingProgress,
    ProcessingStage,
    ProcessingStatus,
    TagStatus,
)
from app.pipelines import extract_text_for_material
from app.services.key_pool import any_provider_ready, get_app_settings
from app.services.resources import (
    harvest_resources,
    merge_card_resources,
    normalize_brand_names,
)

log = get_logger(__name__)


def _refresh_card_after_ocr(material: Material) -> None:
    """After moments atlas rebuild: same merge/ground path as card apply (sync)."""
    from app.services.resources import (
        digest_lines,
        ground_digest_lines,
        is_speech_offtopic,
        speech_unreliable,
    )

    harvest = harvest_resources(
        material.raw_text,
        duration_seconds=getattr(material, "duration_seconds", None),
    )
    tools, links = merge_card_resources(
        material.tools or [], material.links or [], harvest, raw_text=material.raw_text
    )
    material.tools = tools
    # Keep existing verified github links; drop dead ones already filtered at apply
    material.links = links or material.links
    if material.digest:
        material.digest = ground_digest_lines(
            material.digest, tools=tools, raw_text=material.raw_text
        )
    unreliable = speech_unreliable(material.raw_text or "", screen_tools=harvest.tools)
    if not unreliable:
        return
    dig = material.digest or ""
    if is_speech_offtopic(material.raw_text or "", screen_tools=harvest.tools) and harvest.tools:
        real_tools = [t for t in harvest.tools if not re.match(r"^OCR\b", t, re.I)]
        dig_l = dig.lower()
        grounded = any(
            t.lower() in dig_l or t.lower().split()[0] in dig_l for t in real_tools
        )
        if not grounded and real_tools:
            dig = "\n".join(f"- На экране: {t}" for t in real_tools[:3])
        else:
            dig = "\n".join(f"- {ln}" for ln in digest_lines(dig)[:3])
    elif dig:
        dig = "\n".join(f"- {ln}" for ln in digest_lines(dig)[:3])
    material.digest = dig


async def find_duplicate(
    session: AsyncSession,
    content_hash: str | None,
    *,
    user_id: str | None = None,
    scope_user: bool = False,
) -> Material | None:
    """Match by content_hash across all statuses (incl. failed) — never invent a second row.

    When scope_user=True, only match within the same owner (or NULL orphans if user_id is None).
    """
    if not content_hash:
        return None
    stmt = select(Material).where(Material.content_hash == content_hash)
    if scope_user:
        if user_id:
            stmt = stmt.where(Material.user_id == user_id)
        else:
            stmt = stmt.where(Material.user_id.is_(None))
    stmt = stmt.order_by(Material.created_at.asc())
    result = await session.execute(stmt)
    return result.scalars().first()


@dataclass
class CreateMaterialOutcome:
    material: Material
    already_exists: bool = False


def needs_enqueue(material: Material, already_exists: bool = False) -> bool:
    """Whether intake should (re)queue processing for this material."""
    status = material.processing_status
    if status in {ProcessingStatus.FAILED.value, ProcessingStatus.QUEUED.value}:
        return True
    if status in {
        ProcessingStatus.PROCESSING.value,
        ProcessingStatus.AWAITING_FULL.value,
        ProcessingStatus.PREVIEW.value,
    }:
        return False
    if status == ProcessingStatus.DONE.value and (material.summary or already_exists):
        return False
    return not already_exists


async def create_material(
    session: AsyncSession,
    *,
    source_type: str,
    source_url: str | None = None,
    file_path: str | None = None,
    note_text: str | None = None,
    content_hash: str | None = None,
    user_id: str | int | None = None,
    chat_id: int | None = None,
    title: str | None = None,
    entry_kind: str | None = None,
) -> CreateMaterialOutcome:
    from app.models import EntryKind

    kind = (entry_kind or EntryKind.KNOWLEDGE.value).strip().lower()
    if kind not in {EntryKind.KNOWLEDGE.value, EntryKind.TASK.value, EntryKind.RULE.value}:
        kind = EntryKind.KNOWLEDGE.value

    owner = str(user_id) if user_id is not None else None
    dup = await find_duplicate(session, content_hash, user_id=owner, scope_user=True)
    if dup:
        return CreateMaterialOutcome(material=dup, already_exists=True)

    material = Material(
        id=str(uuid4()),
        title=title,
        source_type=source_type,
        entry_kind=kind,
        source_url=source_url,
        file_path=file_path,
        content_hash=content_hash,
        user_id=owner,
        telegram_chat_id=chat_id,
        raw_text=note_text if source_type == "note" else None,
        processing_status=ProcessingStatus.QUEUED.value,
    )
    session.add(material)
    await session.commit()
    await session.refresh(material)
    return CreateMaterialOutcome(material=material, already_exists=False)


async def _taxonomy(session: AsyncSession) -> tuple[list[str], list[str], dict, dict]:
    from app.services.taxonomy import load_taxonomy

    tax = await load_taxonomy(session)
    return tax.categories, tax.approved_tags, tax.cat_by_key, tax.tag_by_key


async def load_global_canon(session: AsyncSession) -> dict:
    """Owner canon from global corrections (aliases / repos / banned_tools)."""
    from app.models import Correction

    canon: dict = {"aliases": {}, "repos": {}, "banned_tools": []}
    try:
        rows = (
            await session.execute(select(Correction).where(Correction.scope == "global"))
        ).scalars().all()
        for r in rows:
            v = r.value if isinstance(r.value, dict) else {"value": r.value}
            if r.field == "alias":
                canon["aliases"][str(v.get("from") or "")] = str(v.get("to") or "")
            elif r.field == "repo":
                canon["repos"][str(v.get("brand") or "")] = str(v.get("url") or "")
            elif r.field == "banned_tool":
                canon["banned_tools"].append(str(v.get("value") or ""))
    except Exception:
        pass
    return canon


async def _apply_card(session: AsyncSession, material: Material, card, cat_map, tag_map) -> None:
    from app.services.taxonomy import resolve_category, resolve_tag

    locked = set(material.locked_fields or [])
    _locked_snapshot = {
        "title": material.title,
        "summary": material.summary,
        "main_idea": material.main_idea,
        "digest": material.digest,
        "tools": material.tools,
        "links": material.links,
        "score": material.usefulness_score,
    }
    canon = await load_global_canon(session)

    if "title" not in locked:
        material.title = normalize_brand_names(card.title or "")
    if "summary" not in locked:
        material.summary = normalize_brand_names(card.summary or "")
    if "main_idea" not in locked:
        material.main_idea = normalize_brand_names(card.main_idea or "")
    from app.services.resources import (
        digest_lines,
        is_speech_offtopic,
        speech_unreliable,
        strip_weak_speech_from_raw,
    )

    harvest = harvest_resources(
        material.raw_text,
        duration_seconds=getattr(material, "duration_seconds", None),
        title=material.title or getattr(card, "title", "") or "",
    )
    _, speech_weak = strip_weak_speech_from_raw(material.raw_text or "")
    speech_off = is_speech_offtopic(material.raw_text or "", screen_tools=harvest.tools)
    unreliable = speech_weak or speech_off or speech_unreliable(
        material.raw_text or "", screen_tools=harvest.tools
    )

    if getattr(card, "digest", None):
        dig = normalize_brand_names(card.digest)
        if unreliable:
            lines = digest_lines(dig)[:3]
            dig = "\n".join(f"- {ln}" for ln in lines) if lines else ""
            # Offtopic: require at least one harvested tool in digest, else rebuild
            if speech_off and harvest.tools:
                real_tools = [t for t in harvest.tools if not re.match(r"^OCR\b", t, re.I)]
                dig_l = dig.lower()
                grounded = any(
                    t.lower() in dig_l or t.lower().split()[0] in dig_l for t in real_tools
                )
                if not grounded and real_tools:
                    dig = "\n".join(f"- На экране: {t}" for t in real_tools[:3])
            # Hard cap again (LLM sometimes returns many • lines that rejoin oddly)
            dig = "\n".join(f"- {ln}" for ln in digest_lines(dig)[:3])
        material.digest = dig
    elif not material.digest and card.summary and not unreliable:
        material.digest = material.summary
    elif not material.digest and unreliable and card.summary:
        if speech_off and harvest.tools:
            material.digest = "\n".join(f"- На экране: {t}" for t in harvest.tools[:3])
        else:
            material.digest = normalize_brand_names((card.summary or "").split(".")[0][:200])
    if "digest" not in locked:
        material.key_ideas = [normalize_brand_names(x) for x in (card.key_ideas or [])]
    material.practical_takeaways = [
        normalize_brand_names(x) for x in (card.practical_takeaways or [])
    ]

    card_links = card.links or []
    if card_links and hasattr(card_links[0], "model_dump"):
        card_links = [x.model_dump() for x in card_links]
    from app.services.resources import salvage_product_names

    card_tools = list(card.tools or []) + salvage_product_names(
        material.raw_text, title=material.title or getattr(card, "title", "") or ""
    )
    tools, links = merge_card_resources(
        card_tools, card_links, harvest, raw_text=material.raw_text, canon=canon
    )
    if "tools" not in locked:
        material.tools = tools
    material.technologies = card.technologies
    if "links" not in locked:
        # Online verification: unverified links stay visible but marked
        try:
            from app.services.verify import verify_links

            links = await verify_links(links)
            # Dead links (verified=no, e.g. 404 slug artifacts) are noise — drop
            links = [li for li in links if li.get("verified") != "no"]
        except Exception:
            pass
        # Live repo resolution (no local lists): product named + GitHub context
        # but no repo URL in evidence → resolve via GitHub Search, similarity-gated.
        try:
            blob_l = (material.raw_text or "").lower()
            has_repo_link = any(
                "github.com/" in str(li.get("url") or "") for li in links
            )
            if not has_repo_link and re.search(
                r"github|гитхаб|репозитор|открыт\w*\s+код|open[- ]?source|опенсорс",
                blob_l,
            ):
                from app.services.verify import (
                    repo_candidate_names,
                    resolve_repo_from_candidates,
                )

                found = await resolve_repo_from_candidates(
                    repo_candidate_names(
                        tools,
                        title=material.title or "",
                        raw_text=material.raw_text or "",
                    ),
                    evidence_text=material.raw_text or "",
                )
                if found:
                    links = merge_link_items(links, [found])
        except Exception:
            pass
        material.links = links
    if material.digest:
        from app.services.resources import ground_digest_lines

        material.digest = ground_digest_lines(
            material.digest, tools=tools, raw_text=material.raw_text
        )
    # Evidence + trust gate + honest degrade
    from app.services.evidence import build_evidence, has_evidence
    from app.services.trust import apply_trust_to_material

    material.evidence = build_evidence(material.raw_text, source_type=material.source_type)
    if not has_evidence(material.raw_text, material.evidence):
        material.digest = "- Мало данных: недостаточно речи/текста для надёжного разбора."
        material.summary = (material.summary or "")[:300] or "Мало данных для разбора."
        card_score = 3
    else:
        card_score = card.usefulness_score
    apply_trust_to_material(material)
    blocks = getattr(card, "code_blocks", None) or []
    material.code_blocks = [
        b.model_dump() if hasattr(b, "model_dump") else b for b in blocks
    ]
    material.usefulness_score = card_score if "score" not in locked else material.usefulness_score
    material.usefulness_reason = card.usefulness_reason
    material.usefulness_updated_at = datetime.now(timezone.utc)

    if "category" in locked:
        cat = next((c for c in cat_map.values() if c.id == material.category_id), None)
        # keep existing category
        material.category_id = material.category_id
    else:
        cat = await resolve_category(session, card.category or "", cat_map, allow_create=False)
        if cat:
            material.category_id = cat.id
        else:
            material.category_id = None

    if "tags" in locked:
        existing_tags = [
            mt.tag.name
            for mt in (material.tags or [])
            if getattr(mt, "tag", None) and getattr(mt.tag, "name", None)
        ]
        return (cat.name if cat else None), existing_tags
    material.tags.clear()
    await session.flush()

    seen_tag_ids: set[int] = set()
    applied_tag_names: list[str] = []
    _tag_ctx = None
    for tag_name in card.tags or []:
        # Capitalized Latin brand/product tags not already in taxonomy must be
        # grounded in evidence (no ChatGPT tag on a Claude video); keep known
        # taxonomy tags and lowercase/Cyrillic concepts.
        tag_key = (tag_name or "").strip().lower()
        if re.search(r"[A-Z][a-z]", tag_name or "") and tag_key not in tag_map:
            try:
                from app.services.grounding import GroundingContext, entity_grounded

                if _tag_ctx is None:
                    _tag_ctx = GroundingContext.from_raw(material.raw_text)
                if not entity_grounded(tag_name, _tag_ctx):
                    continue
            except Exception:
                pass
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
        applied_tag_names.append(tag.name)
        session.add(
            MaterialTag(
                material_id=material.id,
                tag_id=tag.id,
                proposed=(tag.status == TagStatus.PENDING.value),
            )
        )
    return (cat.name if cat else None), applied_tag_names


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


def _has_card_content(material: Material) -> bool:
    return bool(
        (material.summary or "").strip()
        or (material.digest or "").strip()
        or (material.main_idea or "").strip()
    )


async def process_material(session: AsyncSession, material_id: str, *, force_full: bool = False) -> Material:
    settings = get_settings()
    result = await session.execute(
        select(Material)
        .options(
            selectinload(Material.category),
            selectinload(Material.tags).selectinload(MaterialTag.tag),
        )
        .where(Material.id == material_id)
    )
    material = result.scalars().first()
    if not material:
        raise ValueError(f"Material {material_id} not found")

    had_ready_card = _has_card_content(material)
    prev_stage = material.processing_stage

    material.processing_status = ProcessingStatus.PROCESSING.value
    material.processing_stage = ProcessingProgress.FETCH.value
    material.error_message = None
    await session.commit()

    async def _set_progress(step: str) -> None:
        material.processing_stage = step
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

        from app.config import resolve_storage_root

        storage = resolve_storage_root(settings)
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
            on_progress=_set_progress,
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
        if title and (not material.title or material.title.startswith(f"{material.source_type}:")):
            material.title = title
        material.usage_audio_seconds = (material.usage_audio_seconds or 0) + (audio_sec or 0)
        await _set_progress(ProcessingProgress.CARD.value)

        categories, tags, cat_map, tag_map = await _taxonomy(session)
        from app.services.resources import is_speech_offtopic, strip_weak_speech_from_raw

        _cleaned, speech_weak = strip_weak_speech_from_raw(raw_text)
        harvest = harvest_resources(
            raw_text,
            duration_seconds=material.duration_seconds,
        )
        screen_resources = harvest.prompt_block()
        speech_offtopic = (not speech_weak) and is_speech_offtopic(
            raw_text, screen_tools=harvest.tools
        )
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
            speech_weak=speech_weak,
            speech_offtopic=speech_offtopic,
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

        cat_name, applied_tag_names = await _apply_card(session, material, llm.card, cat_map, tag_map)

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
                elif getattr(material, "trust_level", None) == "mcp_safe":
                    # Trust gate passed → always produce teacher pack for MCP
                    from app.services.enrichment import build_explanation

                    if not (material.detailed_explanation or "").strip():
                        await build_explanation(session, material)
                if depth == "compact" and not (material.digest or "").strip():
                    from app.services.enrichment import rebuild_digest

                    await rebuild_digest(session, material)
                if (
                    app_cfg.get("auto_moments")
                    and material.source_type in {"youtube", "instagram", "tiktok", "video"}
                    and (getattr(material, "entry_kind", None) or "knowledge") == "knowledge"
                ):
                    from app.services.moments import build_key_moments

                    await _set_progress(ProcessingProgress.MOMENTS.value)
                    # Do not silently re-download full YouTube video when captions fast-path skipped downloading it
                    has_local_media = bool(material.file_path and Path(material.file_path).exists())
                    allow_dl = has_local_media or material.source_type != "youtube"
                    await build_key_moments(
                        session,
                        material,
                        video_path=material.file_path,
                        allow_download=allow_dl,
                    )
                    # Atlas OCR often improves during moments — refresh tools + offtopic digest
                    _refresh_card_after_ocr(material)
            except Exception as e:
                log.warning("post_enrich_failed", material_id=material_id, error=str(e)[:300])
            material.processing_status = ProcessingStatus.DONE.value

        # Compute embedding after post-enrichment/OCR refresh so vector matches persisted card
        try:
            from app.search.embeddings import compute_embedding, material_embedding_text

            emb_text = material_embedding_text(
                material,
                category_name=cat_name,
                tag_names=applied_tag_names,
            )
            emb = await compute_embedding(emb_text, settings=settings, session=session)
            if emb is not None:
                material.embedding = emb
        except Exception as exc:
            log.warning("material_embedding_failed", material_id=material_id, error=str(exc)[:200])

        # Persist depth stage (preview|full) and guarantee clean error state after completion
        material.processing_stage = stage
        material.error_message = None
        await _refresh_search_vector(session, material.id)
        await session.commit()
        await session.refresh(material)
        try:
            from app.services.graph import invalidate_graph_cache

            invalidate_graph_cache()
        except Exception:
            pass
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
            # Only preserve DONE when a completed card ALREADY existed before this run;
            # first-run failures after saving raw_text/title must strictly be FAILED.
            if had_ready_card and _has_card_content(material):
                material.processing_status = ProcessingStatus.DONE.value
                material.processing_stage = (
                    prev_stage
                    if prev_stage in {ProcessingStage.FULL.value, ProcessingStage.PREVIEW.value}
                    else ProcessingStage.FULL.value
                )
                material.error_message = (
                    f"Повторная обработка не удалась (карточка сохранена): "
                    f"{type(e).__name__}: {str(e)[:600]}"
                )
            else:
                material.processing_status = ProcessingStatus.FAILED.value
                material.processing_stage = None
                material.error_message = f"{type(e).__name__}: {str(e)[:800]}"
            await session.commit()
        raise
