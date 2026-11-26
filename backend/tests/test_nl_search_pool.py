from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import search as search_mod
from app.ai import RateLimitError
from app.models import Base, Material, ProcessingStatus, SourceType
from app.search import answer_question


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def _seed_material(session, mid: str = "m1") -> Material:
    m = Material(
        id=mid,
        source_type=SourceType.NOTE.value,
        title="Промптинг для агентов",
        summary="Краткий гайд по системным промптам и инструментам агентов.",
        main_idea="Структурируй инструкции агента по ролям и ограничениям.",
        raw_text="Системные промпты для AI агентов работают лучше с чёткими примерами.",
        processing_status=ProcessingStatus.DONE.value,
        usefulness_score=9,
    )
    session.add(m)
    await session.commit()
    return m


def _patch_no_env_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        search_mod,
        "get_settings",
        lambda: SimpleNamespace(ai_ready=False, is_sqlite=True),
    )


async def test_answer_question_uses_with_llm_fallback_and_crm_pool(monkeypatch):
    _patch_no_env_settings(monkeypatch)
    engine, factory = await _make_session()
    try:
        async with factory() as session:
            await _seed_material(session)

            fake_client = SimpleNamespace(
                complete_text=AsyncMock(return_value="Ответ из пула ключей [1].")
            )
            fallback_called = {"count": 0}

            async def fake_with_llm_fallback(sess, fn):
                fallback_called["count"] += 1
                assert sess is session
                return await fn(fake_client)

            monkeypatch.setattr(
                "app.services.key_pool.any_provider_ready",
                AsyncMock(return_value=True),
            )
            monkeypatch.setattr("app.ai.with_llm_fallback", fake_with_llm_fallback)

            # Even when .env settings have no keys (ai_ready=False), CRM key pool must work
            answer, hits = await answer_question(session, "промпты для агента")

            assert fallback_called["count"] == 1
            assert fake_client.complete_text.await_count == 1
            assert answer.startswith("Ответ из пула ключей [1].")
            assert "Sources:" in answer
            assert len(hits) == 1
            assert hits[0].id == "m1"
    finally:
        await engine.dispose()


async def test_answer_question_falls_back_cleanly_when_no_ai_keys(monkeypatch):
    _patch_no_env_settings(monkeypatch)
    engine, factory = await _make_session()
    try:
        async with factory() as session:
            await _seed_material(session)

            monkeypatch.setattr(
                "app.services.key_pool.any_provider_ready",
                AsyncMock(return_value=False),
            )
            fallback_mock = AsyncMock()
            monkeypatch.setattr("app.ai.with_llm_fallback", fallback_mock)

            answer, hits = await answer_question(session, "промпты агентов")

            assert fallback_mock.await_count == 0
            assert "Промптинг для агентов" in answer
            assert len(hits) == 1
    finally:
        await engine.dispose()


async def test_answer_question_falls_back_cleanly_when_pool_exhausted(monkeypatch):
    _patch_no_env_settings(monkeypatch)
    engine, factory = await _make_session()
    try:
        async with factory() as session:
            await _seed_material(session)

            monkeypatch.setattr(
                "app.services.key_pool.any_provider_ready",
                AsyncMock(return_value=True),
            )
            monkeypatch.setattr(
                "app.ai.with_llm_fallback",
                AsyncMock(side_effect=RateLimitError("all keys exhausted")),
            )

            answer, hits = await answer_question(session, "промпты агентов")

            assert "Промптинг для агентов" in answer
            assert len(hits) == 1
    finally:
        await engine.dispose()


async def test_answer_question_falls_back_when_llm_returns_empty(monkeypatch):
    _patch_no_env_settings(monkeypatch)
    engine, factory = await _make_session()
    try:
        async with factory() as session:
            await _seed_material(session)

            monkeypatch.setattr(
                "app.services.key_pool.any_provider_ready",
                AsyncMock(return_value=True),
            )
            monkeypatch.setattr(
                "app.ai.with_llm_fallback",
                AsyncMock(return_value="   "),
            )

            answer, hits = await answer_question(session, "промпты агентов")

            assert "Промптинг для агентов" in answer
            assert len(hits) == 1
    finally:
        await engine.dispose()


async def test_answer_question_no_hits_skips_llm(monkeypatch):
    _patch_no_env_settings(monkeypatch)
    engine, factory = await _make_session()
    try:
        async with factory() as session:
            await _seed_material(session)

            ready_mock = AsyncMock(return_value=True)
            monkeypatch.setattr("app.services.key_pool.any_provider_ready", ready_mock)

            answer, hits = await answer_question(session, "квантовая гравитация")

            assert ready_mock.await_count == 0
            assert hits == []
            assert "Не нашёл" in answer
    finally:
        await engine.dispose()
