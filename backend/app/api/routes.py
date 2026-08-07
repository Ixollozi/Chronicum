from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_web_token
from app.api.schemas import (
    ApiKeyCreate,
    ApiKeyOut,
    ApiKeyPatch,
    AppSettingsOut,
    AppSettingsUpdate,
    CategoryCreate,
    CategoryOut,
    ComposerRequest,
    ComposerResponse,
    EnqueueResponse,
    GraphResponse,
    KeysCheckResponse,
    MaterialDeleteRequest,
    MaterialDeleteResponse,
    MaterialDetail,
    MaterialListItem,
    MaterialListResponse,
    NoteCreate,
    ScoreUpdate,
    SearchRequest,
    SearchResponse,
    TagOut,
    TtsRequest,
    UrlCreate,
)
from app.db import get_session
from app.models import Category, Material, MaterialTag, ProcessingStatus, Tag, TagStatus
from app.search import answer_question
from app.services.composer import run_composer
from app.services.graph import build_graph
from app.services.processing import create_material
from app.services.queue import enqueue_material
from app.services.sources import UnsupportedSourceError, classify_url
from app.services.storage import file_sha256, save_bytes, text_sha256, url_sha256

router = APIRouter(prefix="/api", dependencies=[Depends(require_web_token)])


def _tags(m: Material) -> list[str]:
    out = []
    for mt in m.tags or []:
        if mt.tag:
            name = mt.tag.name
            if mt.proposed:
                name = f"{name}?"
            out.append(name)
    return out


def _category_name(m: Material) -> str | None:
    return m.category.name if m.category else None


def to_list_item(m: Material) -> MaterialListItem:
    return MaterialListItem(
        id=m.id,
        title=m.title,
        source_type=m.source_type,
        source_url=m.source_url,
        summary=m.summary,
        main_idea=m.main_idea,
        category=_category_name(m),
        tags=_tags(m),
        usefulness_score=m.usefulness_score,
        processing_status=m.processing_status,
        processing_stage=m.processing_stage,
        created_at=m.created_at,
    )


def to_detail(m: Material) -> MaterialDetail:
    from app.services.audio_media import material_has_audio
    from app.services.resources import coerce_link_items

    base = to_list_item(m)
    return MaterialDetail(
        **base.model_dump(),
        raw_text=m.raw_text,
        key_ideas=m.key_ideas or [],
        practical_takeaways=m.practical_takeaways or [],
        tools=m.tools or [],
        technologies=m.technologies or [],
        links=coerce_link_items(m.links or []),
        code_blocks=getattr(m, "code_blocks", None) or [],
        usefulness_reason=m.usefulness_reason,
        error_message=m.error_message,
        duration_seconds=m.duration_seconds,
        has_audio=material_has_audio(m),
        usage_tokens_in=m.usage_tokens_in or 0,
        usage_tokens_out=m.usage_tokens_out or 0,
        usage_audio_seconds=m.usage_audio_seconds or 0,
        digest=getattr(m, "digest", None),
        detailed_explanation=getattr(m, "detailed_explanation", None),
        objective_evaluation=getattr(m, "objective_evaluation", None),
        ai_opinion=getattr(m, "ai_opinion", None),
        ai_opinion_sources=getattr(m, "ai_opinion_sources", None) or [],
        ai_opinion_at=getattr(m, "ai_opinion_at", None),
        key_moments=getattr(m, "key_moments", None) or [],
    )


async def _enqueue(material_id: str, force_full: bool = False) -> None:
    await enqueue_material(material_id, force_full)

@router.get("/materials", response_model=MaterialListResponse)
async def list_materials(
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = None,
    tag: str | None = None,
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(Material)
        .options(
            selectinload(Material.category),
            selectinload(Material.tags).selectinload(MaterialTag.tag),
        )
        .order_by(Material.created_at.desc())
    )
    count_stmt = select(func.count()).select_from(Material)

    if status_filter:
        stmt = stmt.where(Material.processing_status == status_filter)
        count_stmt = count_stmt.where(Material.processing_status == status_filter)
    if category:
        stmt = stmt.join(Category).where(Category.name.ilike(category))
        count_stmt = count_stmt.join(Category).where(Category.name.ilike(category))
    if tag:
        tag_sub = (
            select(MaterialTag.material_id)
            .join(Tag)
            .where(Tag.name.ilike(tag))
        )
        stmt = stmt.where(Material.id.in_(tag_sub))
        count_stmt = count_stmt.where(Material.id.in_(tag_sub))
    if q:
        like = f"%{q}%"
        cond = (
            Material.title.ilike(like)
            | Material.summary.ilike(like)
            | Material.main_idea.ilike(like)
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().unique().all()
    return MaterialListResponse(items=[to_list_item(m) for m in rows], total=total)


@router.get("/materials/{material_id}", response_model=MaterialDetail)
async def get_material(material_id: str, session: AsyncSession = Depends(get_session)):
    m = (
        await session.execute(
            select(Material)
            .options(
                selectinload(Material.category),
                selectinload(Material.tags).selectinload(MaterialTag.tag),
            )
            .where(Material.id == material_id)
        )
    ).scalars().first()
    if not m:
        raise HTTPException(404, "Not found")
    return to_detail(m)


@router.delete("/materials/{material_id}", response_model=MaterialDeleteResponse)
async def delete_one_material(material_id: str, session: AsyncSession = Depends(get_session)):
    from app.services.delete_material import delete_material

    ok = await delete_material(session, material_id)
    if not ok:
        raise HTTPException(404, "Not found")
    return MaterialDeleteResponse(deleted=[material_id], missing=[], count=1)


@router.post("/materials/delete", response_model=MaterialDeleteResponse)
async def delete_many_materials(body: MaterialDeleteRequest, session: AsyncSession = Depends(get_session)):
    from app.services.delete_material import delete_materials

    result = await delete_materials(session, body.ids)
    if result["count"] == 0:
        raise HTTPException(404, "Nothing deleted")
    return MaterialDeleteResponse(**result)


@router.patch("/materials/{material_id}/score", response_model=MaterialDetail)
async def update_score(
    material_id: str,
    body: ScoreUpdate,
    session: AsyncSession = Depends(get_session),
):
    from datetime import datetime, timezone

    m = (
        await session.execute(
            select(Material)
            .options(
                selectinload(Material.category),
                selectinload(Material.tags).selectinload(MaterialTag.tag),
            )
            .where(Material.id == material_id)
        )
    ).scalars().first()
    if not m:
        raise HTTPException(404, "Not found")
    m.usefulness_score = body.score
    if body.reason and body.reason.strip():
        m.usefulness_reason = body.reason.strip()
    else:
        m.usefulness_reason = ((m.usefulness_reason or "") + " [web]").strip()
    m.usefulness_updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(m)
    return to_detail(m)


@router.post("/materials/{material_id}/retry", response_model=EnqueueResponse)
async def retry_material(material_id: str, session: AsyncSession = Depends(get_session)):
    m = (await session.execute(select(Material).where(Material.id == material_id))).scalars().first()
    if not m:
        raise HTTPException(404, "Not found")
    m.processing_status = ProcessingStatus.QUEUED.value
    m.error_message = None
    await session.commit()
    await _enqueue(material_id)
    return EnqueueResponse(id=material_id, status="queued", message="Re-queued")


@router.post("/materials/{material_id}/process-full", response_model=EnqueueResponse)
async def process_full(material_id: str, session: AsyncSession = Depends(get_session)):
    m = (await session.execute(select(Material).where(Material.id == material_id))).scalars().first()
    if not m:
        raise HTTPException(404, "Not found")
    await _enqueue(material_id, force_full=True)
    return EnqueueResponse(id=material_id, status="queued", message="Full processing queued")


@router.post("/materials/note", response_model=EnqueueResponse)
async def create_note(body: NoteCreate, session: AsyncSession = Depends(get_session)):
    m = await create_material(
        session,
        source_type="note",
        note_text=body.text.strip(),
        content_hash=text_sha256(body.text.strip()),
        title=body.text.strip()[:80],
    )
    if not (m.summary and m.processing_status == ProcessingStatus.DONE.value):
        await _enqueue(m.id)
    return EnqueueResponse(id=m.id, status=m.processing_status, message="Note accepted")


@router.post("/materials/url", response_model=EnqueueResponse)
async def create_url(body: UrlCreate, session: AsyncSession = Depends(get_session)):
    url = body.url.strip()
    try:
        source_type = classify_url(url)
    except UnsupportedSourceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    m = await create_material(
        session,
        source_type=source_type,
        source_url=url,
        content_hash=url_sha256(url),
    )
    if not (m.summary and m.processing_status == ProcessingStatus.DONE.value):
        await _enqueue(m.id)
    return EnqueueResponse(id=m.id, status=m.processing_status, message=f"{source_type} accepted")


@router.post("/materials/upload", response_model=EnqueueResponse)
async def upload_file(
    session: AsyncSession = Depends(get_session),
    file: UploadFile = File(...),
    kind: str | None = Form(default=None),
):
    name = (file.filename or "upload").lower()
    raw = await file.read()
    suffix = Path(name).suffix or ""
    path = save_bytes(raw, suffix)

    mime = file.content_type or ""
    if kind:
        source_type = kind
    elif suffix == ".pdf" or "pdf" in mime:
        source_type = "pdf"
    elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} or mime.startswith("image/"):
        source_type = "image"
    elif (
        suffix
        in {
            ".ogg",
            ".oga",
            ".opus",
            ".mp3",
            ".wav",
            ".m4a",
            ".aac",
            ".flac",
            ".wma",
            ".aiff",
            ".aif",
            ".caf",
            ".amr",
            ".mpga",
            ".mpeg",
            ".mp2",
            ".weba",
        }
        or mime.startswith("audio/")
    ):
        source_type = "voice"
    elif suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"} or mime.startswith("video/"):
        source_type = "video"
    else:
        raise HTTPException(
            400,
            f"Неподдерживаемый файл: {name}. "
            "Можно: PDF, изображение, видео, аудио (mp3/wav/m4a/ogg/flac/aac/opus…).",
        )

    m = await create_material(
        session,
        source_type=source_type,
        file_path=path,
        content_hash=file_sha256(path),
        title=Path(name).name,
    )
    if not (m.summary and m.processing_status == ProcessingStatus.DONE.value):
        await _enqueue(m.id)
    return EnqueueResponse(id=m.id, status=m.processing_status, message=f"{source_type} uploaded")


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest, session: AsyncSession = Depends(get_session)):
    answer, materials = await answer_question(session, body.query.strip())
    # reload with relations for list items
    items = []
    if materials:
        ids = [m.id for m in materials]
        rows = (
            await session.execute(
                select(Material)
                .options(
                    selectinload(Material.category),
                    selectinload(Material.tags).selectinload(MaterialTag.tag),
                )
                .where(Material.id.in_(ids))
            )
        ).scalars().all()
        by_id = {m.id: m for m in rows}
        items = [to_list_item(by_id[i]) for i in ids if i in by_id]
    return SearchResponse(answer=answer, sources=items)


@router.post("/composer", response_model=ComposerResponse)
async def composer(body: ComposerRequest, session: AsyncSession = Depends(get_session)):
    try:
        result = await run_composer(session, body.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    sources: list[MaterialListItem] = []
    if result.sources:
        ids = [m.id for m in result.sources]
        rows = (
            await session.execute(
                select(Material)
                .options(
                    selectinload(Material.category),
                    selectinload(Material.tags).selectinload(MaterialTag.tag),
                )
                .where(Material.id.in_(ids))
            )
        ).scalars().all()
        by_id = {m.id: m for m in rows}
        sources = [to_list_item(by_id[i]) for i in ids if i in by_id]
    return ComposerResponse(
        intent=result.intent,
        message=result.message,
        material_id=result.material_id,
        answer=result.answer,
        sources=sources,
    )


@router.get("/graph", response_model=GraphResponse)
async def graph(session: AsyncSession = Depends(get_session)):
    data = await build_graph(session)
    return GraphResponse(**data)


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Category).order_by(Category.name))).scalars().all()
    return [CategoryOut(id=c.id, name=c.name) for c in rows]


@router.post("/categories", response_model=CategoryOut)
async def create_category(body: CategoryCreate, session: AsyncSession = Depends(get_session)):
    from app.services.taxonomy import load_taxonomy, resolve_category

    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Empty category name")
    tax = await load_taxonomy(session)
    c = await resolve_category(session, name, tax.cat_by_key, allow_create=True)
    if not c:
        raise HTTPException(400, "Invalid category name")
    await session.commit()
    await session.refresh(c)
    return CategoryOut(id=c.id, name=c.name)


@router.get("/tags", response_model=list[TagOut])
async def list_tags(
    session: AsyncSession = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status"),
):
    stmt = select(Tag).order_by(Tag.name)
    if status_filter:
        stmt = stmt.where(Tag.status == status_filter)
    rows = (await session.execute(stmt)).scalars().all()
    return [TagOut(id=t.id, name=t.name, status=t.status) for t in rows]


@router.post("/tags/{tag_id}/approve", response_model=TagOut)
async def approve_tag(tag_id: int, session: AsyncSession = Depends(get_session)):
    t = (await session.execute(select(Tag).where(Tag.id == tag_id))).scalars().first()
    if not t:
        raise HTTPException(404, "Not found")
    t.status = TagStatus.APPROVED.value
    # Approved tags are no longer "proposed" on cards
    links = (
        await session.execute(select(MaterialTag).where(MaterialTag.tag_id == tag_id))
    ).scalars().all()
    for link in links:
        link.proposed = False
    await session.commit()
    await session.refresh(t)
    return TagOut(id=t.id, name=t.name, status=t.status)


@router.post("/tags/{tag_id}/reject", response_model=TagOut)
async def reject_tag(tag_id: int, session: AsyncSession = Depends(get_session)):
    t = (await session.execute(select(Tag).where(Tag.id == tag_id))).scalars().first()
    if not t:
        raise HTTPException(404, "Not found")
    t.status = TagStatus.REJECTED.value
    links = (
        await session.execute(select(MaterialTag).where(MaterialTag.tag_id == tag_id))
    ).scalars().all()
    for link in links:
        await session.delete(link)
    await session.commit()
    await session.refresh(t)
    return TagOut(id=t.id, name=t.name, status=t.status)


# --- Settings & enrichment -------------------------------------------------


async def _load_material(session: AsyncSession, material_id: str) -> Material:
    result = await session.execute(
        select(Material)
        .options(
            selectinload(Material.category),
            selectinload(Material.tags).selectinload(MaterialTag.tag),
        )
        .where(Material.id == material_id)
    )
    m = result.scalars().first()
    if not m:
        raise HTTPException(404, "Not found")
    return m


@router.get("/settings", response_model=AppSettingsOut)
async def get_settings_api(session: AsyncSession = Depends(get_session)):
    from app.services.key_pool import get_app_settings

    data = await get_app_settings(session)
    return AppSettingsOut(**data)


@router.patch("/settings", response_model=AppSettingsOut)
async def patch_settings_api(body: AppSettingsUpdate, session: AsyncSession = Depends(get_session)):
    from app.services.key_pool import patch_app_settings

    try:
        data = await patch_app_settings(
            session,
            default_depth=body.default_depth,
            auto_moments=body.auto_moments,
            moments_vision_gate=body.moments_vision_gate,
            personal_profile=body.personal_profile.model_dump() if body.personal_profile is not None else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return AppSettingsOut(**data)


@router.get("/settings/keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    provider: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    from app.services.key_pool import list_keys

    rows = await list_keys(session, provider)
    return [ApiKeyOut(**r) for r in rows]


@router.post("/settings/keys")
async def create_api_keys(body: ApiKeyCreate, session: AsyncSession = Depends(get_session)):
    from app.services.key_pool import add_keys

    try:
        n = await add_keys(session, body.provider, body.keys, body.label)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"added": n, "message": f"Добавлено ключей: {n}"}


@router.patch("/settings/keys/{key_id}", response_model=ApiKeyOut)
async def patch_api_key(key_id: int, body: ApiKeyPatch, session: AsyncSession = Depends(get_session)):
    from app.services.key_pool import patch_key

    try:
        row = await patch_key(session, key_id, enabled=body.enabled, label=body.label)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e).lower() else 400, str(e)) from e
    return ApiKeyOut(
        id=row.id,
        provider=row.provider,
        label=row.label,
        hint=row.key_hint,
        enabled=row.enabled,
        source=row.source,
        last_ok_at=row.last_ok_at,
        last_error=row.last_error,
    )


@router.delete("/settings/keys/{key_id}")
async def delete_api_key(key_id: int, session: AsyncSession = Depends(get_session)):
    from app.services.key_pool import delete_key

    try:
        await delete_key(session, key_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@router.post("/settings/keys/check", response_model=KeysCheckResponse)
async def check_api_keys(provider: str = Query(...), session: AsyncSession = Depends(get_session)):
    from app.ai import GoogleLLMClient, GroqLLMClient
    from app.config import get_settings as gs
    from app.services.key_pool import acquire_keys, mark_key_result

    if provider not in {"groq", "google"}:
        raise HTTPException(400, "provider must be groq|google")
    settings = gs()
    keys = await acquire_keys(session, provider)
    # Don't rotate away — re-list for check without RR? acquire already rotated; OK
    ok = 0
    failed = 0
    details = []
    for pk in keys:
        try:
            if provider == "groq":
                client = GroqLLMClient(settings, pk.api_key, key_id=pk.id)
            else:
                client = GoogleLLMClient(settings, pk.api_key, key_id=pk.id)
            await client.complete_text("Reply with OK", "ping")
            await mark_key_result(session, pk.id, ok=True)
            ok += 1
            details.append({"id": pk.id, "ok": True, "hint": pk.api_key[-4:]})
        except Exception as e:
            failed += 1
            await mark_key_result(session, pk.id, ok=False, error=str(e)[:200])
            details.append({"id": pk.id, "ok": False, "error": str(e)[:200]})
    return KeysCheckResponse(provider=provider, ok=ok, failed=failed, details=details)


@router.post("/materials/{material_id}/digest", response_model=MaterialDetail)
async def material_digest(material_id: str, session: AsyncSession = Depends(get_session)):
    from app.services.enrichment import rebuild_digest

    m = await _load_material(session, material_id)
    try:
        m = await rebuild_digest(session, m)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.post("/materials/{material_id}/explain", response_model=MaterialDetail)
async def material_explain(material_id: str, session: AsyncSession = Depends(get_session)):
    from app.services.enrichment import build_explanation

    m = await _load_material(session, material_id)
    try:
        m = await build_explanation(session, m)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.post("/materials/{material_id}/evaluate", response_model=MaterialDetail)
async def material_evaluate(material_id: str, session: AsyncSession = Depends(get_session)):
    from app.services.enrichment import build_evaluation

    m = await _load_material(session, material_id)
    try:
        m = await build_evaluation(session, m)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.post("/materials/{material_id}/opinion", response_model=MaterialDetail)
async def material_opinion(material_id: str, session: AsyncSession = Depends(get_session)):
    from app.services.opinion import build_opinion

    m = await _load_material(session, material_id)
    try:
        m = await build_opinion(session, m)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.post("/materials/{material_id}/moments", response_model=MaterialDetail)
async def material_moments(material_id: str, session: AsyncSession = Depends(get_session)):
    from app.services.moments import build_key_moments

    m = await _load_material(session, material_id)
    try:
        m = await build_key_moments(session, m, video_path=m.file_path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.get("/materials/{material_id}/moments/{filename}")
async def serve_moment(material_id: str, filename: str):
    from fastapi.responses import FileResponse

    from app.config import PROJECT_ROOT, get_settings

    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "bad filename")
    settings = get_settings()
    root = Path(settings.storage_path)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    path = root / "materials" / material_id / "moments" / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Not found")
    suffix = path.suffix.lower()
    media = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png" if suffix == ".png" else "application/octet-stream"
    return FileResponse(
        path,
        media_type=media,
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


@router.get("/materials/{material_id}/audio")
async def serve_audio(material_id: str, session: AsyncSession = Depends(get_session)):
    from fastapi.responses import FileResponse

    from app.services.audio_media import audio_media_type, resolve_material_audio

    m = (
        await session.execute(select(Material).where(Material.id == material_id))
    ).scalars().first()
    if not m:
        raise HTTPException(404, "Not found")
    path = resolve_material_audio(m)
    if not path:
        raise HTTPException(404, "No audio for this material")
    return FileResponse(
        path,
        media_type=audio_media_type(path),
        filename=path.name,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'inline; filename="{path.name}"',
        },
    )


@router.get("/tts/voices")
async def tts_voices():
    from app.services.tts import list_voices_public

    return {"voices": list_voices_public()}


@router.post("/tts")
async def synthesize_tts(body: TtsRequest):
    from fastapi.responses import FileResponse, StreamingResponse

    from app.services.tts import (
        cache_path_for,
        iter_speech_audio,
        prepare_for_speech,
        resolve_voice,
        synthesize_speech,
    )

    prepared = prepare_for_speech(body.text)
    if not prepared:
        raise HTTPException(400, "Пустой текст для озвучки")

    key, voice_id = resolve_voice(body.voice)
    cached = cache_path_for(prepared, voice_id)
    if cached.is_file() and cached.stat().st_size > 0:
        return FileResponse(
            cached,
            media_type="audio/mpeg",
            filename=f"tts-{key}.mp3",
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-TTS-Voice": key,
                "X-TTS-Cache": "hit",
                "Content-Disposition": f'inline; filename="tts-{key}.mp3"',
            },
        )

    # Short clips: blocking save is fine and simpler for <audio>
    if len(prepared) <= 280:
        try:
            path, voice_key = await synthesize_speech(body.text, body.voice)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"TTS недоступен: {e}") from e
        return FileResponse(
            path,
            media_type="audio/mpeg",
            filename=f"tts-{voice_key}.mp3",
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-TTS-Voice": voice_key,
                "X-TTS-Cache": "miss",
                "Content-Disposition": f'inline; filename="tts-{voice_key}.mp3"',
            },
        )

    async def gen():
        try:
            async for part in iter_speech_audio(body.text, body.voice):
                yield part
        except Exception as e:
            log = __import__("app.logging_setup", fromlist=["get_logger"]).get_logger(__name__)
            log.warning("tts_stream_failed", error=str(e)[:200])
            raise

    return StreamingResponse(
        gen(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-TTS-Voice": key,
            "X-TTS-Cache": "stream",
            "Content-Disposition": f'inline; filename="tts-{key}.mp3"',
        },
    )
