from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import AuthContext, require_auth, scope_user_id
from app.api.schemas import (
    ApiKeyCreate,
    ApiKeyOut,
    ApiKeyPatch,
    AppSettingsOut,
    AppSettingsUpdate,
    CategoryCreate,
    CategoryOut,
    ClientContextResponse,
    ClientCreate,
    ClientDetail,
    ClientMergeRequest,
    ClientMessageCreate,
    ClientMessageOut,
    ClientMessageUpdate,
    ClientOut,
    ClientUpdate,
    ComposerBatchItem,
    ComposerRequest,
    ComposerResponse,
    CookiesStatusOut,
    CookiesUpdate,
    CorrectionCreate,
    EnqueueResponse,
    GraphResponse,
    KeysCheckResponse,
    MaterialClientLinkCreate,
    MaterialDeleteRequest,
    MaterialDeleteResponse,
    MaterialDetail,
    MaterialListItem,
    MaterialListResponse,
    MaterialPatch,
    MaterialsBusyResponse,
    NoteCreate,
    PasteCommitRequest,
    PastePreviewRequest,
    ScoreUpdate,
    SearchFeedbackCreate,
    SearchFeedbackOut,
    SearchRequest,
    SearchResponse,
    TagOut,
    TtsRequest,
    UrlCreate,
)
from app.db import get_session
from app.models import (
    Category,
    Material,
    MaterialTag,
    ProcessingStatus,
    SearchFeedback,
    SourceType,
    Tag,
    TagStatus,
)
from app.search import answer_question
from app.services.composer import run_composer
from app.services.graph import build_graph, invalidate_graph_cache
from app.services.ownership import ensure_client_access, ensure_material_access, owner_filter
from app.services.processing import create_material, needs_enqueue
from app.services.queue import enqueue_material
from app.services.sources import UnsupportedSourceError, classify_url
from app.services.storage import file_sha256, save_bytes, text_sha256, url_sha256

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


async def _get_material(session: AsyncSession, material_id: str, auth: AuthContext) -> Material:
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
    return ensure_material_access(m, auth)


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
        entry_kind=getattr(m, "entry_kind", None) or "knowledge",
        source_url=m.source_url,
        summary=m.summary,
        main_idea=m.main_idea,
        category=_category_name(m),
        tags=_tags(m),
        usefulness_score=m.usefulness_score,
        processing_status=m.processing_status,
        processing_stage=m.processing_stage,
        trust_level=getattr(m, "trust_level", None),
        analysis_confidence=getattr(m, "analysis_confidence", None),
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
        evidence=getattr(m, "evidence", None) or [],
        locked_fields=getattr(m, "locked_fields", None) or [],
        detailed_explanation=getattr(m, "detailed_explanation", None),
        objective_evaluation=getattr(m, "objective_evaluation", None),
        ai_opinion=getattr(m, "ai_opinion", None),
        ai_opinion_sources=getattr(m, "ai_opinion_sources", None) or [],
        ai_opinion_at=getattr(m, "ai_opinion_at", None),
        key_moments=getattr(m, "key_moments", None) or [],
    )


async def _enqueue(material_id: str, force_full: bool = False) -> None:
    await enqueue_material(material_id, force_full)


@router.get("/materials/busy", response_model=MaterialsBusyResponse)
async def materials_busy(
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    """Queue depth for the current account (admin = all)."""
    q_where = [Material.processing_status == ProcessingStatus.QUEUED.value]
    p_where = [Material.processing_status == ProcessingStatus.PROCESSING.value]
    own = owner_filter(Material.user_id, auth)
    if own is not None:
        q_where.append(own)
        p_where.append(own)
    queued = int(
        (await session.execute(select(func.count()).select_from(Material).where(*q_where))).scalar()
        or 0
    )
    processing = int(
        (await session.execute(select(func.count()).select_from(Material).where(*p_where))).scalar()
        or 0
    )
    return MaterialsBusyResponse(queued=queued, processing=processing, total=queued + processing)


@router.get("/materials", response_model=MaterialListResponse)
async def list_materials(
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
    q: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = None,
    tag: str | None = None,
    source_type: str | None = None,
    entry_kind: str | None = None,
    min_score: int | None = Query(default=None, ge=1, le=10),
    sort: str = Query(default="newest", pattern="^(newest|oldest|score_desc|score_asc)$"),
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Material).options(
        selectinload(Material.category),
        selectinload(Material.tags).selectinload(MaterialTag.tag),
    )
    count_stmt = select(func.count()).select_from(Material)
    own = owner_filter(Material.user_id, auth)
    if own is not None:
        stmt = stmt.where(own)
        count_stmt = count_stmt.where(own)

    if status_filter:
        stmt = stmt.where(Material.processing_status == status_filter)
        count_stmt = count_stmt.where(Material.processing_status == status_filter)
    if source_type:
        stmt = stmt.where(Material.source_type == source_type)
        count_stmt = count_stmt.where(Material.source_type == source_type)
    if entry_kind:
        stmt = stmt.where(Material.entry_kind == entry_kind)
        count_stmt = count_stmt.where(Material.entry_kind == entry_kind)
    if min_score is not None:
        stmt = stmt.where(Material.usefulness_score.is_not(None), Material.usefulness_score >= min_score)
        count_stmt = count_stmt.where(
            Material.usefulness_score.is_not(None), Material.usefulness_score >= min_score
        )
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

    if sort == "oldest":
        stmt = stmt.order_by(Material.created_at.asc())
    elif sort == "score_desc":
        stmt = stmt.order_by(Material.usefulness_score.desc().nullslast(), Material.created_at.desc())
    elif sort == "score_asc":
        stmt = stmt.order_by(Material.usefulness_score.asc().nullslast(), Material.created_at.desc())
    else:
        stmt = stmt.order_by(Material.created_at.desc())

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().unique().all()
    return MaterialListResponse(items=[to_list_item(m) for m in rows], total=total)


@router.get("/materials/{material_id}", response_model=MaterialDetail)
async def get_material(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    m = await _get_material(session, material_id, auth)
    return to_detail(m)


@router.delete("/materials/{material_id}", response_model=MaterialDeleteResponse)
async def delete_one_material(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.delete_material import delete_material

    await _get_material(session, material_id, auth)
    ok = await delete_material(session, material_id)
    if not ok:
        raise HTTPException(404, "Not found")
    return MaterialDeleteResponse(deleted=[material_id], missing=[], count=1)


@router.post("/materials/delete", response_model=MaterialDeleteResponse)
async def delete_many_materials(
    body: MaterialDeleteRequest,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.delete_material import delete_materials

    allowed: list[str] = []
    for mid in body.ids:
        m = (
            await session.execute(select(Material).where(Material.id == mid))
        ).scalars().first()
        try:
            ensure_material_access(m, auth)
            allowed.append(mid)
        except HTTPException:
            continue
    result = await delete_materials(session, allowed)
    if result["count"] == 0:
        raise HTTPException(404, "Nothing deleted")
    return MaterialDeleteResponse(**result)


@router.patch("/materials/{material_id}/score", response_model=MaterialDetail)
async def update_score(
    material_id: str,
    body: ScoreUpdate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from datetime import datetime, timezone

    m = await _get_material(session, material_id, auth)
    m.usefulness_score = body.score
    if body.reason and body.reason.strip():
        m.usefulness_reason = body.reason.strip()
    else:
        m.usefulness_reason = ((m.usefulness_reason or "") + " [web]").strip()
    m.usefulness_updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(m)
    return to_detail(m)


async def _load_material(
    session: AsyncSession, material_id: str, auth: AuthContext | None = None
) -> Material:
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
    if auth is not None:
        return ensure_material_access(m, auth)
    if not m:
        raise HTTPException(404, "Not found")
    return m


@router.patch("/materials/{material_id}", response_model=MaterialDetail)
async def patch_material(
    material_id: str,
    body: MaterialPatch,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    """Owner correction: edited fields become locked canon over LLM output."""
    from app.models import Correction

    m = await _load_material(session, material_id, auth)
    locked = list(m.locked_fields or [])
    field_map = {
        "title": "title",
        "summary": "summary",
        "main_idea": "main_idea",
        "digest": "digest",
        "tools": "tools",
        "links": "links",
        "usefulness_score": "score",
    }
    data = body.model_dump(exclude_none=True)
    for key, value in data.items():
        if key == "note":
            continue
        attr = "usefulness_score" if key == "usefulness_score" else key
        setattr(m, attr, value)
        lock_name = field_map.get(key, key)
        if lock_name not in locked:
            locked.append(lock_name)
        session.add(
            Correction(scope="material", material_id=m.id, field=lock_name, value=value, note=body.note)
        )
    m.locked_fields = locked
    await session.commit()
    await session.refresh(m)
    return to_detail(m)


@router.post("/corrections/global")
async def create_global_correction(
    body: CorrectionCreate, session: AsyncSession = Depends(get_session)
):
    from app.models import Correction

    if body.field not in {"alias", "repo", "banned_tool"}:
        raise HTTPException(400, "field must be alias|repo|banned_tool")
    row = Correction(scope="global", field=body.field, value=body.value, note=body.note)
    session.add(row)
    await session.commit()
    return {"ok": True, "id": row.id}


@router.post("/materials/{material_id}/reprocess")
async def reprocess_material_part(
    material_id: str,
    only: str = Query("full"),
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    """Targeted reprocess: links|digest|evidence|full (full queues pipeline)."""
    m = await _load_material(session, material_id, auth)
    if only == "full":
        m.processing_status = ProcessingStatus.QUEUED.value
        m.processing_stage = None
        m.error_message = None
        await session.commit()
        await _enqueue(material_id, force_full=True)
        return {"id": material_id, "only": only, "status": "queued"}
    if only == "evidence":
        from app.services.evidence import build_evidence
        from app.services.trust import apply_trust_to_material

        m.evidence = build_evidence(m.raw_text, source_type=m.source_type)
        apply_trust_to_material(m)
    elif only == "links":
        from app.services.resources import (
            ground_digest_lines,
            harvest_resources,
            merge_card_resources,
        )
        from app.services.verify import verify_links

        harvest = harvest_resources(m.raw_text, duration_seconds=m.duration_seconds)
        tools, links = merge_card_resources(
            m.tools or [], m.links or [], harvest, raw_text=m.raw_text
        )
        m.tools = tools
        links = await verify_links(links)
        links = [li for li in links if li.get("verified") != "no"]
        # Same live repo resolution as full apply (no local lists)
        try:
            import re as _re

            blob_l = (m.raw_text or "").lower()
            if not any("github.com/" in str(li.get("url") or "") for li in links) and _re.search(
                r"github|гитхаб|репозитор|открыт\w*\s+код|open[- ]?source|опенсорс", blob_l
            ):
                from app.services.resources import merge_link_items
                from app.services.verify import (
                    repo_candidate_names,
                    resolve_repo_from_candidates,
                )

                found = await resolve_repo_from_candidates(
                    repo_candidate_names(
                        list(tools or []),
                        title=m.title or "",
                        raw_text=m.raw_text or "",
                    ),
                    evidence_text=m.raw_text or "",
                )
                if found:
                    links = merge_link_items(links, [found])
        except Exception:
            pass
        m.links = links
        if m.digest:
            m.digest = ground_digest_lines(m.digest, tools=list(tools or []), raw_text=m.raw_text)
    elif only == "digest":
        from app.services.resources import ground_digest_lines

        if m.digest:
            m.digest = ground_digest_lines(m.digest, tools=list(m.tools or []), raw_text=m.raw_text)
    else:
        raise HTTPException(400, "only must be links|digest|evidence|full")
    await session.commit()
    await session.refresh(m)
    return {"id": material_id, "only": only, "status": "done"}


@router.post("/materials/{material_id}/retry", response_model=EnqueueResponse)
async def retry_material(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    m = await _get_material(session, material_id, auth)
    m.processing_status = ProcessingStatus.QUEUED.value
    m.processing_stage = None
    m.error_message = None
    await session.commit()
    await _enqueue(material_id)
    return EnqueueResponse(id=material_id, status="queued", message="Re-queued")


@router.post("/materials/{material_id}/process-full", response_model=EnqueueResponse)
async def process_full(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    m = await _get_material(session, material_id, auth)
    m.processing_status = ProcessingStatus.QUEUED.value
    m.processing_stage = None
    m.error_message = None
    await session.commit()
    await _enqueue(material_id, force_full=True)
    return EnqueueResponse(id=material_id, status="queued", message="Full processing queued")


@router.post("/materials/note", response_model=EnqueueResponse)
async def create_note(
    body: NoteCreate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    out = await create_material(
        session,
        source_type="note",
        note_text=body.text.strip(),
        content_hash=text_sha256(body.text.strip()),
        title=body.text.strip()[:80],
        user_id=scope_user_id(auth),
    )
    m = out.material
    will = needs_enqueue(m, out.already_exists)
    if will:
        if out.already_exists and m.processing_status == ProcessingStatus.FAILED.value:
            m.processing_status = ProcessingStatus.QUEUED.value
            m.error_message = None
            await session.commit()
        await _enqueue(m.id)
    if out.already_exists and not will:
        msg = "Уже в архиве"
    elif out.already_exists:
        msg = "Повторно в очереди"
    else:
        msg = "Note accepted"
    return EnqueueResponse(
        id=m.id,
        status=m.processing_status,
        message=msg,
        already_exists=out.already_exists,
    )


@router.post("/materials/url", response_model=EnqueueResponse)
async def create_url(
    body: UrlCreate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    url = body.url.strip()
    try:
        source_type = classify_url(url)
    except UnsupportedSourceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    out = await create_material(
        session,
        source_type=source_type,
        source_url=url,
        content_hash=url_sha256(url),
        user_id=scope_user_id(auth),
    )
    m = out.material
    will = needs_enqueue(m, out.already_exists)
    if will:
        if out.already_exists and m.processing_status == ProcessingStatus.FAILED.value:
            m.processing_status = ProcessingStatus.QUEUED.value
            m.error_message = None
            await session.commit()
        await _enqueue(m.id)
    if out.already_exists and not will:
        msg = "Уже в архиве"
    elif out.already_exists:
        msg = "Повторно в очереди"
    else:
        msg = f"{source_type} accepted"
    return EnqueueResponse(
        id=m.id,
        status=m.processing_status,
        message=msg,
        already_exists=out.already_exists,
    )


_VALID_SOURCE_TYPES = frozenset({e.value for e in SourceType})
_AUDIO_SUFFIXES = frozenset(
    {
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
)
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})


def _safe_disposition_name(name: str) -> str:
    cleaned = "".join(ch for ch in (name or "file") if 32 <= ord(ch) < 127 and ch not in {'"', "\\", ";", "/"})
    return cleaned[:180] or "file"


@router.post("/materials/upload", response_model=EnqueueResponse)
async def upload_file(
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
    file: UploadFile = File(...),
    kind: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
):
    import asyncio
    import hashlib
    from uuid import uuid4

    from app.config import get_settings
    from app.services.storage import ensure_storage

    name = (file.filename or "upload").lower()
    suffix = Path(name).suffix.lower() if Path(name).suffix else ""
    mime = (file.content_type or "").lower()

    if kind is not None and kind.strip():
        candidate_kind = kind.strip().lower()
        if candidate_kind not in _VALID_SOURCE_TYPES:
            raise HTTPException(400, f"Недопустимый тип материала: {candidate_kind}")
        source_type = candidate_kind
    elif suffix == ".pdf" or "pdf" in mime:
        source_type = "pdf"
    elif suffix in _IMAGE_SUFFIXES or mime.startswith("image/"):
        source_type = "image"
    elif suffix in _AUDIO_SUFFIXES or mime.startswith("audio/"):
        source_type = "voice"
    elif suffix in _VIDEO_SUFFIXES or mime.startswith("video/"):
        source_type = "video"
    else:
        raise HTTPException(
            400,
            f"Неподдерживаемый файл: {name}. "
            "Можно: PDF, изображение, видео, аудио (mp3/wav/m4a/ogg/flac/aac/opus…).",
        )

    settings = get_settings()
    max_bytes = max(1, int(getattr(settings, "max_upload_mb", 200) or 200)) * 1024 * 1024
    root = await asyncio.to_thread(ensure_storage)
    dest = root / f"{uuid4().hex}{suffix}"
    hasher = hashlib.sha256()
    total_written = 0

    def _write_chunk(fp, data: bytes) -> None:
        fp.write(data)

    fp = await asyncio.to_thread(open, dest, "wb")
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > max_bytes:
                raise HTTPException(
                    413,
                    f"Файл превышает максимальный размер ({settings.max_upload_mb} МБ).",
                )
            hasher.update(chunk)
            await asyncio.to_thread(_write_chunk, fp, chunk)
    except Exception:
        await asyncio.to_thread(fp.close)
        dest.unlink(missing_ok=True)
        raise
    else:
        await asyncio.to_thread(fp.close)

    url = (source_url or "").strip() or None
    try:
        out = await create_material(
            session,
            source_type=source_type,
            file_path=str(dest),
            content_hash=hasher.hexdigest(),
            title=Path(name).name,
            source_url=url,
            user_id=scope_user_id(auth),
        )
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    m = out.material
    will = needs_enqueue(m, out.already_exists)
    if will:
        if out.already_exists and m.processing_status == ProcessingStatus.FAILED.value:
            m.processing_status = ProcessingStatus.QUEUED.value
            m.processing_stage = None
            m.error_message = None
            await session.commit()
        await _enqueue(m.id)
    if out.already_exists and not will:
        msg = "Уже в архиве"
    elif out.already_exists:
        msg = "Повторно в очереди"
    else:
        msg = f"{source_type} uploaded"
    return EnqueueResponse(
        id=m.id,
        status=m.processing_status,
        message=msg,
        already_exists=out.already_exists,
    )


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    uid = scope_user_id(auth)
    answer, materials = await answer_question(
        session, body.query.strip(), user_id=uid, scope_user=uid is not None
    )
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


@router.post("/search/feedback", response_model=SearchFeedbackOut)
async def create_search_feedback(
    body: SearchFeedbackCreate,
    session: AsyncSession = Depends(get_session),
):
    flag = bool(body.helpful)
    fb = SearchFeedback(
        query=body.query.strip()[:500],
        answer=(body.answer or "").strip() or None,
        source_ids=[s.strip() for s in body.source_ids if s and s.strip()][:30],
        helpful=flag,
        useful=flag,
        comment=(body.comment or "").strip() or None,
    )
    session.add(fb)
    await session.commit()
    await session.refresh(fb)
    return SearchFeedbackOut(
        id=fb.id,
        query=fb.query,
        helpful=fb.helpful,
        useful=fb.useful,
        ok=True,
        source_ids=fb.source_ids or [],
        comment=fb.comment,
        created_at=fb.created_at,
    )


@router.get("/search/feedback", response_model=list[SearchFeedbackOut])
async def list_search_feedback(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
):
    rows = (
        await session.execute(
            select(SearchFeedback).order_by(SearchFeedback.created_at.desc(), SearchFeedback.id.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        SearchFeedbackOut(
            id=r.id,
            query=r.query,
            helpful=r.helpful,
            useful=r.useful,
            ok=True,
            source_ids=r.source_ids or [],
            comment=r.comment,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/composer", response_model=ComposerResponse)
async def composer(
    body: ComposerRequest,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    try:
        result = await run_composer(
            session, body.text, mode=body.mode, user_id=scope_user_id(auth)
        )
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
    items = [
        ComposerBatchItem(id=it["id"], status=it["status"], already_exists=it.get("already_exists", False))
        for it in (result.items or [])
    ]
    return ComposerResponse(
        intent=result.intent,
        message=result.message,
        material_id=result.material_id,
        answer=result.answer,
        sources=sources,
        already_exists=bool(result.already_exists),
        items=items,
    )


@router.get("/graph", response_model=GraphResponse)
async def graph(
    session: AsyncSession = Depends(get_session),
    light: bool = Query(default=False),
    auth: AuthContext = Depends(require_auth),
):
    uid = scope_user_id(auth)
    data = await build_graph(
        session,
        include_semantic=not light,
        user_id=uid,
        scope_user=uid is not None,
    )
    return GraphResponse(**data)


@router.post("/embeddings/reindex")
async def reindex_material_embeddings(
    force: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
):
    from app.search.embeddings import reindex_embeddings

    res = await reindex_embeddings(session, force=force, limit=limit)
    if res.get("updated"):
        invalidate_graph_cache()
    return res


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
    invalidate_graph_cache()
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
    invalidate_graph_cache()
    await session.refresh(t)
    return TagOut(id=t.id, name=t.name, status=t.status)


# --- Settings & enrichment -------------------------------------------------


@router.get("/settings", response_model=AppSettingsOut)
async def get_settings_api(session: AsyncSession = Depends(get_session)):
    from app.services.key_pool import get_app_settings

    data = await get_app_settings(session)
    return AppSettingsOut(**data)


@router.get("/settings/cookies", response_model=CookiesStatusOut)
async def get_cookies_status():
    import asyncio
    from dataclasses import asdict

    from app.services.cookies import inspect_cookies_file

    info = await asyncio.to_thread(inspect_cookies_file)
    return CookiesStatusOut(**asdict(info))


@router.post("/settings/cookies", response_model=CookiesStatusOut)
async def upload_cookies_content(body: CookiesUpdate):
    import asyncio
    from dataclasses import asdict

    from app.services.cookies import save_cookies_content

    try:
        info = await asyncio.to_thread(save_cookies_content, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return CookiesStatusOut(**asdict(info))


@router.delete("/settings/cookies", response_model=CookiesStatusOut)
async def clear_cookies_content():
    import asyncio
    from dataclasses import asdict

    from app.services.cookies import delete_cookies_file

    info = await asyncio.to_thread(delete_cookies_file)
    return CookiesStatusOut(**asdict(info))


@router.patch("/settings", response_model=AppSettingsOut)
async def patch_settings_api(body: AppSettingsUpdate, session: AsyncSession = Depends(get_session)):
    from app.services.key_pool import patch_app_settings

    try:
        data = await patch_app_settings(
            session,
            default_depth=body.default_depth,
            auto_moments=body.auto_moments,
            video_full=body.video_full,
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
            try:
                await client.complete_text("Reply with OK", "ping")
            finally:
                await client.aclose()
            await mark_key_result(session, pk.id, ok=True)
            ok += 1
            details.append({"id": pk.id, "ok": True, "hint": pk.api_key[-4:]})
        except Exception as e:
            failed += 1
            await mark_key_result(session, pk.id, ok=False, error=str(e)[:200])
            details.append({"id": pk.id, "ok": False, "error": str(e)[:200]})
    await session.commit()
    return KeysCheckResponse(provider=provider, ok=ok, failed=failed, details=details)


@router.post("/materials/{material_id}/digest", response_model=MaterialDetail)
async def material_digest(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.enrichment import rebuild_digest

    m = await _load_material(session, material_id, auth)
    try:
        m = await rebuild_digest(session, m)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.post("/materials/{material_id}/explain", response_model=MaterialDetail)
async def material_explain(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.enrichment import build_explanation

    m = await _load_material(session, material_id, auth)
    try:
        m = await build_explanation(session, m)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.post("/materials/{material_id}/evaluate", response_model=MaterialDetail)
async def material_evaluate(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.enrichment import build_evaluation

    m = await _load_material(session, material_id, auth)
    try:
        m = await build_evaluation(session, m)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.post("/materials/{material_id}/opinion", response_model=MaterialDetail)
async def material_opinion(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.opinion import build_opinion

    m = await _load_material(session, material_id, auth)
    try:
        m = await build_opinion(session, m)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.post("/materials/{material_id}/moments", response_model=MaterialDetail)
async def material_moments(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.moments import build_key_moments

    m = await _load_material(session, material_id, auth)
    try:
        m = await build_key_moments(session, m, video_path=m.file_path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return to_detail(m)


@router.get("/materials/{material_id}/moments/{filename}")
async def serve_moment(
    material_id: str,
    filename: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    import re

    from fastapi.responses import FileResponse

    from app.config import get_settings, resolve_storage_root

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", material_id or ""):
        raise HTTPException(400, "bad material_id")
    if (
        not filename
        or Path(filename).name != filename
        or ".." in filename
        or "/" in filename
        or "\\" in filename
        or any(ord(c) < 32 for c in filename)
    ):
        raise HTTPException(400, "bad filename")
    if not auth.is_admin:
        await _get_material(session, material_id, auth)

    settings = get_settings()
    materials_root = (resolve_storage_root(settings) / "materials").resolve()
    expected_dir = (materials_root / material_id / "moments").resolve()
    if not expected_dir.is_relative_to(materials_root):
        raise HTTPException(400, "bad material_id")
    path = (expected_dir / filename).resolve()
    if not path.is_relative_to(expected_dir):
        raise HTTPException(400, "bad filename")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Not found")
    suffix = path.suffix.lower()
    media = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png" if suffix == ".png" else "application/octet-stream"
    safe_name = _safe_disposition_name(filename)
    return FileResponse(
        path,
        media_type=media,
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": f'inline; filename="{safe_name}"',
        },
    )


@router.get("/materials/{material_id}/audio")
async def serve_audio(
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from fastapi.responses import FileResponse

    await _get_material(session, material_id, auth)

    from app.services.audio_media import audio_media_type, resolve_material_audio

    m = (
        await session.execute(select(Material).where(Material.id == material_id))
    ).scalars().first()
    if not m:
        raise HTTPException(404, "Not found")
    path = resolve_material_audio(m)
    if not path:
        raise HTTPException(404, "No audio for this material")
    safe_name = _safe_disposition_name(path.name)
    return FileResponse(
        path,
        media_type=audio_media_type(path),
        filename=safe_name,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'inline; filename="{safe_name}"',
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


def _client_out(c, *, message_count: int | None = None) -> ClientOut:
    if message_count is not None:
        n = message_count
        stale = {"summary_stale": False, "messages_since_summary": 0}
    else:
        from sqlalchemy import inspect as sa_inspect

        # Avoid lazy-load of messages in sync serializer (MissingGreenlet).
        if "messages" in sa_inspect(c).unloaded:
            n = 0
            stale = {"summary_stale": False, "messages_since_summary": 0}
        else:
            n = len(c.messages or [])
            from app.services.clients import summary_staleness

            stale = summary_staleness(c)
    return ClientOut(
        id=c.id,
        display_name=c.display_name,
        aliases=c.aliases or [],
        contacts=c.contacts or {},
        context_line=c.context_line,
        canon_facts=getattr(c, "canon_facts", None),
        summary=c.summary,
        summary_version=int(c.summary_version or 0),
        summary_updated_at=c.summary_updated_at,
        summary_up_to_message_id=getattr(c, "summary_up_to_message_id", None),
        summary_stale=bool(stale.get("summary_stale")),
        messages_since_summary=int(stale.get("messages_since_summary") or 0),
        created_at=c.created_at,
        updated_at=c.updated_at,
        message_count=n,
    )


def _msg_out(m) -> ClientMessageOut:
    return ClientMessageOut(
        id=m.id,
        body=m.body,
        source=m.source,
        speaker=getattr(m, "speaker", "unknown") or "unknown",
        speaker_name=getattr(m, "speaker_name", None),
        kind=getattr(m, "kind", "text") or "text",
        event_at=getattr(m, "event_at", None),
        created_at=m.created_at,
    )


def _client_detail(c) -> ClientDetail:
    base = _client_out(c)
    msgs = sorted(
        c.messages or [],
        key=lambda m: (getattr(m, "event_at", None) or m.created_at, m.created_at),
    )
    return ClientDetail(**base.model_dump(), messages=[_msg_out(m) for m in msgs])


@router.get("/clients", response_model=list[ClientOut])
async def api_list_clients(
    q: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import list_clients

    uid = scope_user_id(auth)
    rows = await list_clients(
        session, q=q, limit=limit, user_id=uid, scope_user=not auth.is_admin
    )
    return [_client_out(c) for c in rows]


@router.post("/clients", response_model=ClientOut)
async def api_create_client(
    body: ClientCreate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import DuplicateClientError, create_client

    try:
        c = await create_client(
            session,
            display_name=body.display_name,
            aliases=body.aliases,
            contacts=body.contacts,
            context_line=body.context_line,
            canon_facts=body.canon_facts,
            force=body.force,
            user_id=scope_user_id(auth),
        )
    except DuplicateClientError as e:
        raise HTTPException(409, {"error": "possible_duplicate", "candidates": e.candidates}) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _client_out(c, message_count=0)


@router.get("/clients/resolve")
async def api_resolve_client(
    q: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import resolve_client_v2

    uid = scope_user_id(auth)
    res = await resolve_client_v2(
        session, q, user_id=uid, scope_user=not auth.is_admin
    )
    if res["status"] == "ambiguous":
        return {"status": "ambiguous", "candidates": res["candidates"]}
    if res["client"]:
        return {"status": "exact", "client": _client_out(res["client"]).model_dump()}
    return {"status": "not_found", "candidates": []}


@router.get("/clients/resolve/context", response_model=ClientContextResponse)
async def api_resolve_client_context(
    q: str = Query(..., min_length=1),
    n: int = Query(20, ge=1, le=50),
    search: str | None = None,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    """Resolve by name/alias/id — primary agent entry for «что с Иваном?»."""
    from app.services.clients import AmbiguousClientError, client_context_pack

    try:
        uid = scope_user_id(auth)
        pack = await client_context_pack(
            session, q, n=n, search_q=search, user_id=uid, scope_user=not auth.is_admin
        )
    except AmbiguousClientError as e:
        raise HTTPException(409, {"error": "ambiguous", "candidates": e.candidates}) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return ClientContextResponse(**pack)


@router.post("/clients/merge", response_model=ClientOut)
async def api_merge_clients(
    body: ClientMergeRequest,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import get_client, merge_clients

    ensure_client_access(await get_client(session, body.from_id), auth)
    ensure_client_access(await get_client(session, body.into_id), auth)
    try:
        c = await merge_clients(session, body.from_id, body.into_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _client_out(c)


@router.get("/clients/{client_id}", response_model=ClientDetail)
async def api_get_client(
    client_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import get_client

    c = await get_client(session, client_id)
    ensure_client_access(c, auth)
    return _client_detail(c)


@router.patch("/clients/{client_id}", response_model=ClientOut)
async def api_update_client(
    client_id: str,
    body: ClientUpdate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import get_client, update_client

    ensure_client_access(await get_client(session, client_id), auth)

    try:
        c = await update_client(session, client_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return _client_out(c)


@router.delete("/clients/{client_id}")
async def api_delete_client(
    client_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import delete_client, get_client

    ensure_client_access(await get_client(session, client_id), auth)
    if not await delete_client(session, client_id):
        raise HTTPException(404, "Client not found")
    return {"ok": True}


@router.post("/clients/{client_id}/messages", response_model=ClientDetail)
async def api_append_client_message(
    client_id: str,
    body: ClientMessageCreate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import append_message, get_client

    ensure_client_access(await get_client(session, client_id), auth)
    try:
        c, _msg = await append_message(
            session,
            client_id,
            body.body,
            source=body.source,
            speaker=body.speaker,
            speaker_name=body.speaker_name,
            kind=body.kind,
            refresh_summary=body.refresh_summary,
        )
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from e
    return _client_detail(c)


@router.patch("/clients/{client_id}/messages/{message_id}", response_model=ClientDetail)
async def api_update_client_message(
    client_id: str,
    message_id: str,
    body: ClientMessageUpdate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import get_client, update_message

    ensure_client_access(await get_client(session, client_id), auth)
    try:
        await update_message(session, client_id, message_id, body.body)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    c = await get_client(session, client_id)
    return _client_detail(c)


@router.delete("/clients/{client_id}/messages/{message_id}", response_model=ClientDetail)
async def api_delete_client_message(
    client_id: str,
    message_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import delete_message, get_client

    ensure_client_access(await get_client(session, client_id), auth)
    if not await delete_message(session, client_id, message_id):
        raise HTTPException(404, "Message not found")
    c = await get_client(session, client_id)
    return _client_detail(c)


@router.post("/clients/{client_id}/paste/preview")
async def api_paste_preview(
    client_id: str,
    body: PastePreviewRequest,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import get_client, paste_preview

    ensure_client_access(await get_client(session, client_id), auth)
    prev = paste_preview(body.text)
    prev["messages"] = [
        {
            "speaker_name": m.get("speaker_name"),
            "body": m["body"][:400],
            "event_at": m["event_at"].isoformat() if m.get("event_at") else None,
        }
        for m in prev["messages"]
    ]
    prev["date_from"] = prev["date_from"].isoformat() if prev.get("date_from") else None
    prev["date_to"] = prev["date_to"].isoformat() if prev.get("date_to") else None
    return prev


@router.post("/clients/{client_id}/paste")
async def api_paste_commit(
    client_id: str,
    body: PasteCommitRequest,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import commit_paste, get_client

    ensure_client_access(await get_client(session, client_id), auth)
    try:
        c, added, skipped = await commit_paste(
            session, client_id, body.text, speaker_roles=body.speaker_roles, source=body.source
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    detail = _client_detail(c)
    return {**detail.model_dump(), "added": added, "skipped_duplicates": skipped}


@router.post("/clients/{client_id}/materials")
async def api_link_material(
    client_id: str,
    body: MaterialClientLinkCreate,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.models import MaterialClientLink
    from app.services.clients import get_client

    ensure_client_access(await get_client(session, client_id), auth)
    await _get_material(session, body.material_id, auth)
    exists = (
        await session.execute(
            select(MaterialClientLink).where(
                MaterialClientLink.client_id == client_id,
                MaterialClientLink.material_id == body.material_id,
            )
        )
    ).scalars().first()
    if not exists:
        session.add(
            MaterialClientLink(material_id=body.material_id, client_id=client_id, relation=body.relation)
        )
        await session.commit()
    return {"ok": True}


@router.delete("/clients/{client_id}/materials/{material_id}")
async def api_unlink_material(
    client_id: str,
    material_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.models import MaterialClientLink
    from app.services.clients import get_client

    ensure_client_access(await get_client(session, client_id), auth)
    row = (
        await session.execute(
            select(MaterialClientLink).where(
                MaterialClientLink.client_id == client_id,
                MaterialClientLink.material_id == material_id,
            )
        )
    ).scalars().first()
    if row:
        await session.delete(row)
        await session.commit()
    return {"ok": True}


@router.post("/clients/{client_id}/summary/refresh", response_model=ClientOut)
async def api_refresh_client_summary(
    client_id: str,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import get_client, refresh_client_summary

    ensure_client_access(await get_client(session, client_id), auth)
    try:
        c = await refresh_client_summary(session, client_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return _client_out(c)


@router.get("/clients/{client_id}/context", response_model=ClientContextResponse)
async def api_client_context(
    client_id: str,
    n: int = Query(20, ge=1, le=50),
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
    auth: AuthContext = Depends(require_auth),
):
    from app.services.clients import AmbiguousClientError, client_context_pack

    try:
        uid = scope_user_id(auth)
        pack = await client_context_pack(
            session, client_id, n=n, search_q=q, user_id=uid, scope_user=not auth.is_admin
        )
    except AmbiguousClientError as e:
        raise HTTPException(409, {"error": "ambiguous", "candidates": e.candidates}) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return ClientContextResponse(**pack)
