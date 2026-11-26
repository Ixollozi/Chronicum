from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.routes import _tags, approve_tag, list_tags, reject_tag
from app.models import Base, Category, Material, MaterialTag, SourceType, Tag, TagStatus
from app.services.processing import _apply_card, _taxonomy
from app.services.taxonomy import merge_duplicate_taxonomy


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _sample_card(tags: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        title="Тестовая карточка",
        summary="Краткое описание",
        main_idea="Главная идея",
        key_ideas=["Идея 1"],
        practical_takeaways=["Шаг 1"],
        tools=[],
        technologies=[],
        links=[],
        code_blocks=[],
        category="AI и агенты",
        tags=tags,
        usefulness_score=8,
        usefulness_reason="Полезно",
    )


async def test_apply_card_creates_new_tags_as_pending_and_skips_rejected():
    engine, factory = await _make_db()
    try:
        async with factory() as session:
            session.add(Category(name="AI и агенты"))
            session.add(Tag(name="rag", status=TagStatus.APPROVED.value))
            session.add(Tag(name="spam-tag", status=TagStatus.REJECTED.value))
            m = Material(id="mat-1", source_type=SourceType.NOTE.value, raw_text="text")
            session.add(m)
            await session.commit()

            m = (
                await session.execute(
                    select(Material)
                    .options(selectinload(Material.tags))
                    .where(Material.id == "mat-1")
                )
            ).scalars().one()

            _, _, cat_map, tag_map = await _taxonomy(session)
            card = _sample_card(["rag", "новый-тег", "spam-tag"])
            await _apply_card(session, m, card, cat_map, tag_map)
            await session.commit()

            all_tags = {
                t.name: t.status
                for t in (await session.execute(select(Tag))).scalars().all()
            }
            assert all_tags["rag"] == TagStatus.APPROVED.value
            assert all_tags["новый-тег"] == TagStatus.PENDING.value
            assert all_tags["spam-tag"] == TagStatus.REJECTED.value

            loaded = (
                await session.execute(
                    select(Material)
                    .options(selectinload(Material.tags).selectinload(MaterialTag.tag))
                    .where(Material.id == "mat-1")
                    .execution_options(populate_existing=True)
                )
            ).scalars().first()
            assert loaded is not None
            by_name = {mt.tag.name: mt.proposed for mt in loaded.tags}
            assert by_name == {"rag": False, "новый-тег": True}
            assert "spam-tag" not in by_name

            # Serialized tag list marks proposed tags with "?"
            rendered = _tags(loaded)
            assert "rag" in rendered
            assert "новый-тег?" in rendered
    finally:
        await engine.dispose()


async def test_merge_duplicate_taxonomy_preserves_pending_by_default():
    engine, factory = await _make_db()
    try:
        async with factory() as session:
            t_single = Tag(name="уникальный-тег", status=TagStatus.PENDING.value)
            t_dup_1 = Tag(name="AI Agents", status=TagStatus.PENDING.value)
            t_dup_2 = Tag(name="ai agents", status=TagStatus.PENDING.value)
            t_mix_1 = Tag(name="Python", status=TagStatus.APPROVED.value)
            t_mix_2 = Tag(name="python", status=TagStatus.PENDING.value)
            session.add_all([t_single, t_dup_1, t_dup_2, t_mix_1, t_mix_2])
            await session.commit()

            stats = await merge_duplicate_taxonomy(session)
            await session.commit()
            assert stats["tags_merged"] == 2

            rows = (await session.execute(select(Tag))).scalars().all()
            by_lower = {t.name.lower(): t.status for t in rows}
            assert by_lower["уникальный-тег"] == TagStatus.PENDING.value
            assert by_lower["ai agents"] == TagStatus.PENDING.value
            assert by_lower["python"] == TagStatus.APPROVED.value
    finally:
        await engine.dispose()


async def test_api_tag_approve_and_reject_endpoints_update_links():
    engine, factory = await _make_db()
    try:
        async with factory() as session:
            t_approve = Tag(name="mcp-protocol", status=TagStatus.PENDING.value)
            t_reject = Tag(name="junk-tag", status=TagStatus.PENDING.value)
            m = Material(id="mat-2", source_type=SourceType.NOTE.value, raw_text="text")
            session.add_all([t_approve, t_reject, m])
            await session.flush()
            session.add_all(
                [
                    MaterialTag(material_id=m.id, tag_id=t_approve.id, proposed=True),
                    MaterialTag(material_id=m.id, tag_id=t_reject.id, proposed=True),
                ]
            )
            await session.commit()

            pending = await list_tags(session=session, status_filter="pending")
            assert {t.name for t in pending} == {"mcp-protocol", "junk-tag"}

            await approve_tag(t_approve.id, session=session)
            await reject_tag(t_reject.id, session=session)

            approved = await list_tags(session=session, status_filter="approved")
            rejected = await list_tags(session=session, status_filter="rejected")
            pending_after = await list_tags(session=session, status_filter="pending")
            assert [t.name for t in approved] == ["mcp-protocol"]
            assert [t.name for t in rejected] == ["junk-tag"]
            assert pending_after == []

            loaded = (
                await session.execute(
                    select(Material)
                    .options(selectinload(Material.tags).selectinload(MaterialTag.tag))
                    .where(Material.id == "mat-2")
                )
            ).scalars().first()
            assert loaded is not None
            assert _tags(loaded) == ["mcp-protocol"]
    finally:
        await engine.dispose()
