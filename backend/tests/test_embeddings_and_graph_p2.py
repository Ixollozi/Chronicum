"""Tests for P2.3 (embeddings + hybrid search) and P2.2 (richer knowledge graph)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import Base
from app.models import Category, Material, ProcessingStatus
from app.search import embeddings, fts_search
from app.services.graph import build_graph


def test_local_embed_text_similarity_and_normalization():
    v1 = embeddings.local_embed_text("Оптимизация Postgres индексов и ускорение SQL запросов")
    v2 = embeddings.local_embed_text("Как ускорить SQL запросы с помощью индексов PostgreSQL")
    v_other = embeddings.local_embed_text("Рецепт домашней пасты карбонара с пармезаном и сливками")

    assert len(v1) == embeddings.LOCAL_EMBED_DIM
    norm = math.sqrt(sum(x * x for x in v1))
    assert abs(norm - 1.0) < 0.01

    sim_related = embeddings.cosine_similarity(v1, v2)
    sim_unrelated = embeddings.cosine_similarity(v1, v_other)
    assert sim_related > 0.35
    assert sim_related > sim_unrelated + 0.25


@pytest.mark.asyncio
async def test_compute_embedding_modes_and_google_fallback(monkeypatch):
    off_cfg = SimpleNamespace(embedding_provider="off", google_ai_api_key="", google_embedding_model="text-embedding-004")
    assert await embeddings.compute_embedding("hello world", settings=off_cfg) is None

    local_cfg = SimpleNamespace(embedding_provider="local", google_ai_api_key="", google_embedding_model="text-embedding-004")
    res_local = await embeddings.compute_embedding("FastAPI async workers", settings=local_cfg)
    assert res_local is not None
    assert res_local["model"] == embeddings.LOCAL_EMBED_MODEL
    assert res_local["dim"] == embeddings.LOCAL_EMBED_DIM

    # Google provider success
    async def fake_google_ok(text: str, *, api_key: str, model: str = "text-embedding-004", timeout: float = 15.0):
        assert api_key == "AIzaRealKey123456"
        return [0.6, 0.8, 0.0]

    monkeypatch.setattr(embeddings, "google_embed_text", fake_google_ok)
    google_cfg = SimpleNamespace(
        embedding_provider="google",
        google_ai_api_key="AIzaRealKey123456",
        google_embedding_model="text-embedding-004",
    )
    res_google = await embeddings.compute_embedding("FastAPI async workers", settings=google_cfg)
    assert res_google == {"model": "google:text-embedding-004", "dim": 3, "vec": [0.6, 0.8, 0.0]}

    # Google provider failure gracefully falls back to local
    async def fake_google_fail(*args, **kwargs):
        return None

    monkeypatch.setattr(embeddings, "google_embed_text", fake_google_fail)
    res_fallback = await embeddings.compute_embedding("FastAPI async workers", settings=google_cfg)
    assert res_fallback is not None
    assert res_fallback["model"] == embeddings.LOCAL_EMBED_MODEL


@pytest.mark.asyncio
async def test_hybrid_search_finds_semantic_paraphrase_and_rejects_unrelated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHRONICUM_LITE", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'hybrid.db'}")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    get_settings.cache_clear()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hybrid.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with Session() as session:
            cat = Category(name="Программирование")
            session.add(cat)
            await session.flush()

            m1 = Material(
                title="Оптимизация PostgreSQL индексов",
                source_type="article",
                summary="Индексы B-Tree и GIN ускоряют выборки в PostgreSQL",
                main_idea="Используй EXPLAIN ANALYZE для диагностики медленных запросов",
                tools=["PostgreSQL", "pgAdmin"],
                technologies=["SQL", "B-Tree"],
                category_id=cat.id,
                usefulness_score=9,
                processing_status=ProcessingStatus.DONE.value,
            )
            m2 = Material(
                title="Домашняя выпечка на закваске",
                source_type="note",
                summary="Как испечь хрустящий хлеб в духовке",
                main_idea="Ферментация теста 12 часов в холодильнике",
                usefulness_score=6,
                processing_status=ProcessingStatus.DONE.value,
            )
            m1.embedding = await embeddings.compute_embedding(embeddings.material_embedding_text(m1))
            m2.embedding = await embeddings.compute_embedding(embeddings.material_embedding_text(m2))
            session.add_all([m1, m2])
            await session.commit()

            # Query with slight typo / subword overlap ("PostgreSQL" misspelled as "Postgresqll" + "индексы")
            hits = await fts_search(session, "ускорение Postgresqll индексами")
            assert hits
            assert hits[0].material.id == m1.id

            # Completely unrelated query must still return empty
            empty_hits = await fts_search(session, "квантовая гравитация космология")
            assert empty_hits == []
    finally:
        await engine.dispose()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_graph_includes_shared_tool_and_semantic_material_links(tmp_path, monkeypatch):
    monkeypatch.setenv("CHRONICUM_LITE", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    get_settings.cache_clear()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with Session() as session:
            cat = Category(name="AI")
            session.add(cat)
            await session.flush()

            m1 = Material(
                title="Пайплайн монтажа Reels с Whisper и ffmpeg",
                source_type="youtube",
                summary="Автоматические субтитры Whisper и нарезка видео через ffmpeg",
                main_idea="Скрипт на Python автоматизирует монтаж коротких роликов",
                tools=["ffmpeg", "Whisper"],
                technologies=["Python"],
                category_id=cat.id,
                usefulness_score=9,
                processing_status=ProcessingStatus.DONE.value,
            )
            m2 = Material(
                title="Автоматизация нарезки видео через ffmpeg и Whisper",
                source_type="article",
                summary="Субтитры через Whisper и пакетная обработка роликов ffmpeg",
                main_idea="Конвейер обработки видео на Python без ручного монтажа",
                tools=["FFmpeg", "Whisper"],
                technologies=["Python"],
                category_id=cat.id,
                usefulness_score=8,
                processing_status=ProcessingStatus.DONE.value,
            )
            m3 = Material(
                title="Керамика и гончарный круг дома",
                source_type="note",
                summary="Обжиг глины и выбор глазури",
                main_idea="Базовые шаги для начинающих керамистов",
                tools=["Гончарный круг"],
                processing_status=ProcessingStatus.DONE.value,
            )
            for m in (m1, m2, m3):
                m.embedding = await embeddings.compute_embedding(embeddings.material_embedding_text(m))
            session.add_all([m1, m2, m3])
            await session.commit()

            data = await build_graph(session)
            links = data["links"]

            tool_links = [l for l in links if l["reason"] == "shared_tool"]
            sem_links = [l for l in links if l["reason"] == "semantic"]

            assert any({l["source"], l["target"]} == {m1.id, m2.id} for l in tool_links)
            pair_tool = next(l for l in tool_links if {l["source"], l["target"]} == {m1.id, m2.id})
            assert pair_tool["weight"] == 3
            assert "Whisper" in (pair_tool.get("label") or "")

            assert any({l["source"], l["target"]} == {m1.id, m2.id} for l in sem_links)
            pair_sem = next(l for l in sem_links if {l["source"], l["target"]} == {m1.id, m2.id})
            assert pair_sem["similarity"] >= 0.36

            # Unrelated material m3 should not be linked to m1/m2 via tool or semantic edges
            assert not any(m3.id in {l["source"], l["target"]} for l in tool_links)
            assert not any(m3.id in {l["source"], l["target"]} for l in sem_links)
    finally:
        await engine.dispose()
        get_settings.cache_clear()
