from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Category, Material, MaterialTag, ProcessingStatus, SourceType, Tag, TagStatus
from app.search import fts_search
from app.search.stemmer import sql_like_variants, stem_word, tokenize_and_stem


def test_russian_stemmer_collapses_inflections_and_yo():
    # Noun cases & plural
    assert stem_word("нейросеть") == stem_word("нейросети")
    assert stem_word("нейросеть") == stem_word("нейросетях")
    assert stem_word("нейросеть") == stem_word("нейросетей")

    assert stem_word("агенты") == stem_word("агента")
    assert stem_word("агенты") == stem_word("агентов")
    assert stem_word("агенты") == stem_word("агентами")

    # Verbs / verbal nouns
    assert stem_word("программирование") == stem_word("программирования")
    assert stem_word("разработку") == stem_word("разработки")

    # ё -> е normalization
    assert stem_word("ёлка") == stem_word("елки")
    assert stem_word("нашёл") == stem_word("нашел")

    # Irregular high-frequency verb roots
    assert stem_word("найти") == stem_word("нашёл") == stem_word("нашли") == stem_word("найдёшь")

    # Lightweight English stemming
    assert stem_word("prompts") == stem_word("prompting") == stem_word("prompted") == "prompt"
    assert stem_word("agents") == stem_word("agent")


def test_sql_like_variants_cover_cyrillic_case_and_yo():
    variants = sql_like_variants("нейросет")
    assert "%нейросет%" in variants
    assert "%Нейросет%" in variants
    assert "%НЕЙРОСЕТ%" in variants

    yo_variants = sql_like_variants("елка")
    assert "%елка%" in yo_variants
    assert "%ёлка%" in yo_variants
    assert "%Ёлка%" in yo_variants


def test_tokenize_and_stem_extracts_stems():
    tokens = tokenize_and_stem("Как и где найти лучшие промпты для нейросетей?")
    assert stem_word("найти") in tokens
    assert stem_word("нейросетей") in tokens
    assert stem_word("промпты") in tokens


async def test_sqlite_search_matches_russian_inflections_case_and_weights_fields():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            cat = Category(name="Искусственный интеллект")
            tag = Tag(name="нейросети", status=TagStatus.APPROVED.value)
            session.add_all([cat, tag])
            await session.flush()

            m_title = Material(
                id="m-title",
                source_type=SourceType.NOTE.value,
                title="Нейросетях и агентах в продакшене",
                summary="Практический разбор архитектуры",
                raw_text="Подробная заметка о моделях и маршрутизации.",
                category_id=cat.id,
                processing_status=ProcessingStatus.DONE.value,
                usefulness_score=8,
            )
            m_body = Material(
                id="m-body",
                source_type=SourceType.NOTE.value,
                title="Общие заметки за неделю",
                summary="Разные мысли",
                raw_text="Вчера мы нашли отличный промпт для нейросетей и протестировали Ёлки.",
                processing_status=ProcessingStatus.DONE.value,
                usefulness_score=7,
            )
            m_other = Material(
                id="m-other",
                source_type=SourceType.NOTE.value,
                title="Кулинарные рецепты",
                summary="Паста карбонара",
                raw_text="Ингредиенты: спагетти, пармезан, бекон.",
                processing_status=ProcessingStatus.DONE.value,
                usefulness_score=9,
            )
            session.add_all([m_title, m_body, m_other])
            await session.flush()
            session.add(MaterialTag(material_id=m_title.id, tag_id=tag.id, proposed=False))
            await session.commit()

            # Inflected + case-insensitive Cyrillic match ("нейросеть" matches "Нейросетях" & "нейросетей")
            hits = await fts_search(session, "нейросеть")
            hit_ids = [h.material.id for h in hits]
            assert "m-title" in hit_ids
            assert "m-body" in hit_ids
            assert "m-other" not in hit_ids
            # Title + tag match should rank higher than raw_text-only match
            assert hit_ids[0] == "m-title"

            # Irregular verb + inflected noun ("найти промпты" -> "нашли ... промпт")
            verb_hits = await fts_search(session, "найти промпты")
            assert [h.material.id for h in verb_hits] == ["m-body"]

            # ё/е equivalence ("елка" -> "Ёлки")
            yo_hits = await fts_search(session, "елка")
            assert [h.material.id for h in yo_hits] == ["m-body"]

            # Category inflection match ("интеллекта" -> category "Искусственный интеллект")
            cat_hits = await fts_search(session, "интеллекта")
            assert [h.material.id for h in cat_hits] == ["m-title"]
    finally:
        await engine.dispose()
