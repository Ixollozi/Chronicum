"""End-to-end API regression test covering auth, ingest, processing, tags, search, feedback, filters, graph, cookies, and deletion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.schemas import KnowledgeCard, UsefulnessSignals
from app.api import routes as api_routes
from app.config import get_settings
from app.db import Base, get_session
from app.main import app
from app.models import Material, ProcessingStatus
from app.services import processing


@pytest.mark.asyncio
async def test_e2e_api_ingest_process_search_feedback_filters_graph_cookies_delete(
    tmp_path: Path, monkeypatch
):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}"
    monkeypatch.setenv("CHRONICUM_LITE", "true")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "files"))
    monkeypatch.setenv("WEB_API_TOKEN", "e2e-test-token-999")
    monkeypatch.setenv("ALLOW_QUERY_TOKEN", "false")
    monkeypatch.setenv("ALLOW_PLACEHOLDER_SECRETS", "false")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.delenv("MEDIA_COOKIES_FILE", raising=False)
    monkeypatch.delenv("MEDIA_COOKIES_FROM_BROWSER", raising=False)
    get_settings.cache_clear()

    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    # Enqueue runs synchronously in-test via process_material with a deterministic stubbed analyzer
    async def fake_enqueue(material_id: str, force_full: bool = False):
        async with Session() as s:
            await processing.process_material(s, material_id, force_full=force_full)

    call_idx = 0

    async def fake_analyze(raw_text: str, **kwargs):
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            card = KnowledgeCard(
                title="Оптимизация PostgreSQL с индексами",
                summary="Практическое руководство по индексам B-Tree и GIN в PostgreSQL.",
                main_idea="Используй EXPLAIN ANALYZE перед добавлением индексов.",
                category="Программирование",
                tags=["postgresql", "fresh-pending-tag"],
                usefulness_signals=UsefulnessSignals(
                    actionability=5, novelty=4, specificity=5, reuse_likelihood=5, personal_fit=5
                ),
                usefulness_score=9,
                usefulness_reason="Критично для ускорения БД",
                tools=["PostgreSQL", "pgAdmin"],
                technologies=["SQL"],
                key_ideas=["B-Tree для равенства и диапазонов"],
                practical_takeaways=["Проверить медленные запросы"],
            )
        else:
            card = KnowledgeCard(
                title="Индексирование и тюнинг SQL в PostgreSQL",
                summary="Разбор планов выполнения SQL и выбора индексов в PostgreSQL.",
                main_idea="Индексы ускоряют чтение, но замедляют запись.",
                category="Программирование",
                tags=["postgresql"],
                usefulness_signals=UsefulnessSignals(
                    actionability=3, novelty=3, specificity=3, reuse_likelihood=3, personal_fit=3
                ),
                usefulness_score=6,
                usefulness_reason="Базовый обзор",
                tools=["PostgreSQL"],
                technologies=["SQL"],
            )
        return SimpleNamespace(card=card, tokens_in=120, tokens_out=80, provider="groq", model="stub")

    async def fake_any_provider_ready(_session):
        return True

    async def fake_with_llm_fallback(_session, fn):
        class _Client:
            async def complete_text(self, _sys: str, user: str) -> str:
                return "Индексы B-Tree и GIN ускоряют запросы в PostgreSQL."

        return await fn(_Client())

    monkeypatch.setattr(api_routes, "_enqueue", fake_enqueue)
    monkeypatch.setattr(processing, "analyze_material", fake_analyze)
    monkeypatch.setattr(processing, "any_provider_ready", fake_any_provider_ready)
    import app.ai as app_ai
    import app.services.key_pool as key_pool

    monkeypatch.setattr(app_ai, "with_llm_fallback", fake_with_llm_fallback)
    monkeypatch.setattr(key_pool, "any_provider_ready", fake_any_provider_ready)

    headers = {"Authorization": "Bearer e2e-test-token-999"}
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Auth guard (missing header & disallowed query token)
            r_unauth = await client.get("/api/materials")
            assert r_unauth.status_code == 401
            r_query_tok = await client.get("/api/materials?token=e2e-test-token-999")
            assert r_query_tok.status_code == 401

            # 2. Ingest two notes -> processed synchronously with embeddings
            r1 = await client.post(
                "/api/materials/note",
                headers=headers,
                json={"text": "Заметка про индексы B-Tree и GIN в PostgreSQL и EXPLAIN ANALYZE."},
            )
            assert r1.status_code == 200
            m1_id = r1.json()["id"]

            r2 = await client.post(
                "/api/materials/note",
                headers=headers,
                json={"text": "Вторая заметка про тюнинг SQL и планы выполнения запросов в PostgreSQL."},
            )
            assert r2.status_code == 200
            m2_id = r2.json()["id"]

            # Verify embeddings were computed and persisted
            async with Session() as s:
                m1_db = (await s.execute(select(Material).where(Material.id == m1_id))).scalar_one()
                assert m1_db.processing_status == ProcessingStatus.DONE.value
                assert isinstance(m1_db.embedding, dict)
                assert m1_db.embedding["dim"] == 256

            # 3. Tag moderation flow
            r_pending = await client.get("/api/tags?status=pending", headers=headers)
            assert r_pending.status_code == 200
            pending_tags = r_pending.json()
            fresh = next(t for t in pending_tags if t["name"] == "fresh-pending-tag")
            r_approve = await client.post(f"/api/tags/{fresh['id']}/approve", headers=headers)
            assert r_approve.status_code == 200
            assert r_approve.json()["status"] == "approved"

            # 4. Richer archive filters & sorting
            r_high = await client.get(
                "/api/materials",
                headers=headers,
                params={"source_type": "note", "entry_kind": "knowledge", "min_score": 8, "sort": "score_desc"},
            )
            assert r_high.status_code == 200
            assert r_high.json()["total"] == 1
            assert r_high.json()["items"][0]["id"] == m1_id

            r_asc = await client.get(
                "/api/materials",
                headers=headers,
                params={"sort": "score_asc"},
            )
            assert [it["id"] for it in r_asc.json()["items"]] == [m2_id, m1_id]

            # 5. Hybrid search + search feedback persistence
            r_search = await client.post(
                "/api/search",
                headers=headers,
                json={"query": "как ускорить SQL запросы PostgreSQL"},
            )
            assert r_search.status_code == 200
            s_data = r_search.json()
            assert "PostgreSQL" in s_data["answer"]
            assert any(src["id"] == m1_id for src in s_data["sources"])

            r_fb = await client.post(
                "/api/search/feedback",
                headers=headers,
                json={
                    "query": "как ускорить SQL запросы PostgreSQL",
                    "answer": s_data["answer"],
                    "source_ids": [src["id"] for src in s_data["sources"]],
                    "helpful": True,
                    "comment": "Точный ответ",
                },
            )
            assert r_fb.status_code == 200
            assert r_fb.json()["helpful"] is True

            r_fb_list = await client.get("/api/search/feedback", headers=headers)
            assert r_fb_list.status_code == 200
            assert len(r_fb_list.json()) == 1
            assert r_fb_list.json()[0]["comment"] == "Точный ответ"

            # 6. Richer graph includes shared_tool + semantic material links
            r_graph = await client.get("/api/graph", headers=headers)
            assert r_graph.status_code == 200
            g_links = r_graph.json()["links"]
            assert any(
                l["reason"] == "shared_tool" and {l["source"], l["target"]} == {m1_id, m2_id}
                for l in g_links
            )
            assert any(
                l["reason"] == "semantic" and {l["source"], l["target"]} == {m1_id, m2_id}
                for l in g_links
            )

            # 7. Cookies settings API (invalid rejected, valid saved, deleted cleanly)
            r_ck_bad = await client.post(
                "/api/settings/cookies",
                headers=headers,
                json={"content": "not valid cookies"},
            )
            assert r_ck_bad.status_code == 400

            r_ck_ok = await client.post(
                "/api/settings/cookies",
                headers=headers,
                json={
                    "content": "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsid\tval\n"
                },
            )
            assert r_ck_ok.status_code == 200
            assert r_ck_ok.json()["configured"] is True
            assert r_ck_ok.json()["platforms"]["instagram"] is True

            r_ck_del = await client.delete("/api/settings/cookies", headers=headers)
            assert r_ck_del.status_code == 200
            assert r_ck_del.json()["configured"] is False

            # 8. Delete materials
            r_del = await client.delete(f"/api/materials/{m1_id}", headers=headers)
            assert r_del.status_code == 200
            assert r_del.json()["count"] == 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        get_settings.cache_clear()
