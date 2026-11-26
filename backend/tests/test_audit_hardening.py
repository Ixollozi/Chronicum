from __future__ import annotations

import importlib
import os
import shutil
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai import PooledTranscribeClient
from app.api import routes as api_routes
from app.config import PROJECT_ROOT, Settings, resolve_storage_root
from app.db import Base, configure_sqlite_engine
from app.main import app as fastapi_app
from app.models import (
    Category,
    Material,
    ProcessingStatus,
)
from app.pipelines import article as article_mod
from app.pipelines import pdf as pdf_mod
from app.search import _semantic_candidates
from app.search.embeddings import (
    LOCAL_EMBED_MODEL,
    compute_embedding,
    local_embed_text,
    material_embedding_text,
    reindex_embeddings,
)
from app.services import delete_material as delete_mod
from app.services import graph as graph_mod
from app.services import key_pool as key_pool_mod
from app.services import moments as moments_mod
from app.services import processing as processing_mod
from app.services import storage as storage_mod
from app.services import tts as tts_mod


async def _make_session_factory(tmp_path: Path, name: str = "test.db"):
    db_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    configure_sqlite_engine(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


# ---------------------------------------------------------------------------
# C1: Full Alembic schema parity vs SQLAlchemy ORM + Docker config
# ---------------------------------------------------------------------------


def test_c1_alembic_upgrade_matches_orm_schema_parity(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "alembic_parity.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    from app.config import get_settings

    get_settings.cache_clear()
    try:
        cfg = AlembicConfig(str(PROJECT_ROOT / "backend" / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        get_settings.cache_clear()

    sync_engine = create_engine(f"sqlite:///{db_file}")
    try:
        insp = inspect(sync_engine)
        alembic_tables = set(insp.get_table_names()) - {"alembic_version"}
        orm_tables = set(Base.metadata.tables.keys())
        assert orm_tables <= alembic_tables, f"Missing tables in Alembic: {orm_tables - alembic_tables}"

        for table_name, table in Base.metadata.tables.items():
            alembic_cols = {c["name"] for c in insp.get_columns(table_name)}
            orm_cols = {c.name for c in table.columns}
            missing = orm_cols - alembic_cols
            assert not missing, f"Table {table_name} missing columns in Alembic: {missing}"
    finally:
        sync_engine.dispose()


def test_c1_and_m4_docker_and_sw_hardening():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "\n  migrate:\n" in compose
    assert "service_completed_successfully" in compose
    assert "STORAGE_PATH: /data/files" in compose
    assert "--reload" not in compose
    assert "NEXT_PUBLIC_WEB_API_TOKEN" not in compose

    web_df = (PROJECT_ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_WEB_API_TOKEN" not in web_df

    backend_df = (PROJECT_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "poppler-utils" in backend_df
    assert "mcp" in backend_df

    sw = (PROJECT_ROOT / "web" / "public" / "sw.js").read_text(encoding="utf-8")
    assert 'url.pathname === "/mcp"' in sw or '"/mcp/"' in sw
    assert 'req.mode === "navigate"' in sw


# ---------------------------------------------------------------------------
# C2: /mcp auth & internal token from Settings/.env + placeholder hardening
# ---------------------------------------------------------------------------


async def test_c2_mcp_auth_uses_settings_token_when_env_unset(monkeypatch):
    mcp_src = str(PROJECT_ROOT / "mcp" / "src")
    if mcp_src not in sys.path:
        sys.path.insert(0, mcp_src)

    from chronicum_mcp import config as mcp_config
    from chronicum_mcp.http_auth import BearerAuthMiddleware

    monkeypatch.delenv("CHRONICUM_TOKEN", raising=False)
    monkeypatch.delenv("WEB_API_TOKEN", raising=False)
    mcp_config.set_runtime_token(None)

    from app import mcp_http

    fake_settings = Settings(
        web_api_token="from-dotenv-secret-token",
        allow_placeholder_secrets=False,
    )
    monkeypatch.setattr("app.config.get_settings", lambda: fake_settings)
    monkeypatch.setattr(mcp_http, "get_settings", lambda: fake_settings)

    # Sync runtime token as build_mcp_asgi() does
    tok, allow_ph = mcp_http._current_mcp_auth_policy()
    assert tok == "from-dotenv-secret-token"
    assert allow_ph is False
    assert mcp_config.api_token() == "from-dotenv-secret-token"

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    wrapped = BearerAuthMiddleware(inner, token_provider=mcp_http._current_mcp_auth_policy)

    async def _call(headers: list[tuple[bytes, bytes]]) -> int:
        status_holder = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            if msg["type"] == "http.response.start":
                status_holder.append(msg["status"])

        await wrapped({"type": "http", "path": "/mcp", "headers": headers}, receive, send)
        return status_holder[0]

    assert await _call([]) == 401
    assert await _call([(b"authorization", b"Bearer wrong")]) == 401
    assert await _call([(b"authorization", b"bearer from-dotenv-secret-token")]) == 200

    # Hardened mode with default/empty token must fail closed with 503
    weak_settings = Settings(
        web_api_token="chronicum-dev-token",
        allow_placeholder_secrets=False,
    )
    monkeypatch.setattr("app.config.get_settings", lambda: weak_settings)
    monkeypatch.setattr(mcp_http, "get_settings", lambda: weak_settings)
    assert await _call([(b"authorization", b"Bearer chronicum-dev-token")]) == 503

    mcp_config.set_runtime_token(None)


# ---------------------------------------------------------------------------
# C3: Path traversal guard in serve_moment + sanitized Content-Disposition
# ---------------------------------------------------------------------------


async def test_c3_serve_moment_blocks_material_id_traversal_and_sanitizes_header(
    tmp_path: Path, monkeypatch
):
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"SECRET")
    storage_root = tmp_path / "storage"
    valid_dir = storage_root / "materials" / "mat_123" / "moments"
    valid_dir.mkdir(parents=True)
    valid_file = valid_dir / "moment_01.jpg"
    valid_file.write_bytes(b"\xff\xd8\xff\xe0jpg")

    fake_settings = Settings(storage_path=str(storage_root), web_api_token="tok-c3")
    monkeypatch.setattr("app.config.get_settings", lambda: fake_settings)
    monkeypatch.setattr("app.api.deps.get_settings", lambda: fake_settings)

    transport = httpx.ASGITransport(app=fastapi_app)
    headers = {"Authorization": "Bearer tok-c3"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Traversal via material_id containing ..
        r1 = await client.get("/api/materials/..%2F..%2F/moments/secret.jpg", headers=headers)
        assert r1.status_code in (400, 404)

        # Direct call to route function with traversal material_id
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await api_routes.serve_moment("../../etc", "passwd")
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc2:
            await api_routes.serve_moment("mat_123", "../secret.jpg")
        assert exc2.value.status_code == 400

        # Valid moment succeeds with clean Content-Disposition
        r_ok = await client.get("/api/materials/mat_123/moments/moment_01.jpg", headers=headers)
        assert r_ok.status_code == 200
        assert r_ok.content == b"\xff\xd8\xff\xe0jpg"
        assert 'filename="moment_01.jpg"' in r_ok.headers.get("content-disposition", "")


# ---------------------------------------------------------------------------
# H1: Unified STORAGE_PATH resolution independent of CWD
# ---------------------------------------------------------------------------


def test_h1_storage_path_resolution_matches_across_modules_from_backend_cwd(
    tmp_path: Path, monkeypatch
):
    rel_storage = f"./.tmp_h1_test_{tmp_path.name}"
    fake_settings = Settings(storage_path=rel_storage)
    monkeypatch.setattr("app.config.get_settings", lambda: fake_settings)

    expected = (PROJECT_ROOT / rel_storage).resolve()
    monkeypatch.chdir(PROJECT_ROOT / "backend")
    try:
        assert resolve_storage_root(fake_settings) == expected
        assert storage_mod.ensure_storage() == expected
        assert moments_mod._moments_dir("abc123") == expected / "materials" / "abc123" / "moments"
    finally:
        if expected.exists():
            shutil.rmtree(expected, ignore_errors=True)


# ---------------------------------------------------------------------------
# H2: SQLite PRAGMAs + Telegram bot DB key check + long note stays a note
# ---------------------------------------------------------------------------


async def test_h2_sqlite_pragmas_wal_busy_timeout_foreign_keys(tmp_path: Path):
    engine, factory = await _make_session_factory(tmp_path, "pragmas.db")
    try:
        async with factory() as session:
            jm = (await session.execute(text("PRAGMA journal_mode"))).scalar_one()
            bt = (await session.execute(text("PRAGMA busy_timeout"))).scalar_one()
            fk = (await session.execute(text("PRAGMA foreign_keys"))).scalar_one()
            assert str(jm).lower() == "wal"
            assert int(bt) >= 15000
            assert int(fk) == 1
    finally:
        await engine.dispose()


async def test_h2_bot_ai_ready_with_db_key_and_long_note_saved_as_note(tmp_path: Path, monkeypatch):
    bot_main = importlib.import_module("app.bot.main")

    engine, factory = await _make_session_factory(tmp_path, "bot.db")
    try:
        empty_env_settings = Settings(
            groq_api_key="replace_me",
            google_ai_api_key="",
            telegram_owner_id=42,
        )
        monkeypatch.setattr(bot_main, "get_settings", lambda: empty_env_settings)
        monkeypatch.setattr(bot_main, "SessionLocal", factory)
        monkeypatch.setattr("app.services.key_pool.get_settings", lambda: empty_env_settings)

        assert await bot_main._ai_is_ready() is False

        async with factory() as session:
            await key_pool_mod.add_keys(session, "groq", "gsk_db_only_key_1234567890", label="db")

        assert await bot_main._ai_is_ready() is True

        # Verify long note (>280 chars without '?') is saved as note, NOT routed to search
        created_calls = []
        search_calls = []

        async def _fake_create_material(session, **kw):
            created_calls.append(kw)
            return SimpleNamespace(id="note-1", summary=None, processing_status="queued")

        async def _fake_enqueue(mid, force_full=False):
            return None

        async def _fake_answer_question(session, q):
            search_calls.append(q)
            return "nope", []

        monkeypatch.setattr(bot_main, "create_material", _fake_create_material)
        monkeypatch.setattr(bot_main, "enqueue", _fake_enqueue)
        monkeypatch.setattr(bot_main, "answer_question", _fake_answer_question)

        dp = bot_main.create_dispatcher()
        text_handler = dp.message.handlers[-1].callback

        replies = []

        class _FakeMsg:
            text = "Это длинная заметка без вопросительного знака. " * 12
            from_user = SimpleNamespace(id=42)
            chat = SimpleNamespace(id=100)

            async def answer(self, txt, **_kw):
                replies.append(txt)

        assert len(_FakeMsg.text) > 280
        await text_handler(_FakeMsg())
        assert len(created_calls) == 1
        assert created_calls[0]["source_type"] == "note"
        assert search_calls == []
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# H3: Bounded streaming upload, validation before write, cleanup on error
# ---------------------------------------------------------------------------


async def test_h3_upload_validates_before_write_and_enforces_size_limit(
    tmp_path: Path, monkeypatch
):
    engine, factory = await _make_session_factory(tmp_path, "upload.db")
    storage_root = tmp_path / "uploads"
    storage_root.mkdir()
    fake_settings = Settings(
        storage_path=str(storage_root),
        web_api_token="tok-h3",
        max_upload_mb=1,
    )
    monkeypatch.setattr("app.config.get_settings", lambda: fake_settings)
    monkeypatch.setattr("app.api.deps.get_settings", lambda: fake_settings)
    monkeypatch.setattr(api_routes, "_enqueue", AsyncMock())

    async def _override_session():
        async with factory() as s:
            yield s

    from app.db import get_session

    fastapi_app.dependency_overrides[get_session] = _override_session
    try:
        transport = httpx.ASGITransport(app=fastapi_app)
        headers = {"Authorization": "Bearer tok-h3"}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1) Invalid kind rejected before writing any file
            r_bad_kind = await client.post(
                "/api/materials/upload",
                headers=headers,
                files={"file": ("doc.pdf", b"%PDF-1.4 hi", "application/pdf")},
                data={"kind": "not_a_real_type"},
            )
            assert r_bad_kind.status_code == 400
            assert list(storage_root.iterdir()) == []

            # 2) Unsupported extension rejected without orphaned file
            r_bad_ext = await client.post(
                "/api/materials/upload",
                headers=headers,
                files={"file": ("malware.exe", b"MZ...", "application/octet-stream")},
            )
            assert r_bad_ext.status_code == 400
            assert list(storage_root.iterdir()) == []

            # 3) Oversized file (> 1 MB) returns 413 and cleans up partial file
            big_payload = b"%PDF" + (b"x" * (1024 * 1024 + 512))
            r_big = await client.post(
                "/api/materials/upload",
                headers=headers,
                files={"file": ("huge.pdf", big_payload, "application/pdf")},
            )
            assert r_big.status_code == 413
            assert list(storage_root.iterdir()) == []

            # 4) Valid upload succeeds
            r_ok = await client.post(
                "/api/materials/upload",
                headers=headers,
                files={"file": ("small.pdf", b"%PDF-1.4 small", "application/pdf")},
            )
            assert r_ok.status_code == 200
            assert len(list(storage_root.iterdir())) == 1
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


# ---------------------------------------------------------------------------
# H4: SSRF + response-size guard in extract_article
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:8000/api/settings",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "file:///etc/passwd",
        "ftp://example.com/file.txt",
    ],
)
async def test_h4_ssrf_blocks_private_loopback_metadata_and_non_http(bad_url: str):
    with pytest.raises(article_mod.UnsafeURLError):
        await article_mod.validate_public_http_url(bad_url)


async def test_h4_extract_article_blocks_redirect_to_private_ip_and_oversized_body(monkeypatch):
    orig_validate = article_mod.validate_public_http_url

    async def _fake_validate(url: str) -> str:
        if "public.example" in url:
            return url
        return await orig_validate(url)

    monkeypatch.setattr(article_mod, "validate_public_http_url", _fake_validate)

    # Case 1: Redirect to 127.0.0.1 is blocked
    def _redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/secret"})

    real_client = httpx.AsyncClient

    def _patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_redirect_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(article_mod.httpx, "AsyncClient", _patched_client)
    with pytest.raises(article_mod.UnsafeURLError):
        await article_mod.extract_article("https://public.example/start")

    # Case 2: Oversized response triggers ValueError
    def _big_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body>" + (b"a" * 4096) + b"</body></html>",
        )

    def _patched_big_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_big_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(article_mod.httpx, "AsyncClient", _patched_big_client)
    with pytest.raises(ValueError, match="exceeds 1024 bytes"):
        await article_mod.extract_article("https://public.example/huge", max_bytes=1024)


# ---------------------------------------------------------------------------
# H6: auto_moments must not download full video after YouTube captions fast-path
# ---------------------------------------------------------------------------


async def test_h6_auto_moments_does_not_download_after_youtube_captions_fast_path(
    tmp_path: Path, monkeypatch
):
    engine, factory = await _make_session_factory(tmp_path, "h6.db")
    try:
        async with factory() as session:
            session.add(Category(name="Dev"))
            m = Material(
                source_type="youtube",
                source_url="https://youtu.be/captions123",
                processing_status=ProcessingStatus.QUEUED.value,
            )
            session.add(m)
            await session.commit()
            mid = m.id

        resolve_spy = AsyncMock(side_effect=AssertionError("Must not download full YouTube video"))
        monkeypatch.setattr(moments_mod, "resolve_video_path", resolve_spy)
        monkeypatch.setattr(processing_mod, "any_provider_ready", AsyncMock(return_value=True))
        monkeypatch.setattr(
            processing_mod,
            "extract_text_for_material",
            AsyncMock(return_value=("[0:05] Useful caption content", "Caption Video", 120.0, 0.0, None)),
        )
        from app.ai import LLMResult
        from app.ai.schemas import KnowledgeCard, UsefulnessSignals

        monkeypatch.setattr(
            processing_mod,
            "analyze_material",
            AsyncMock(
                return_value=LLMResult(
                    card=KnowledgeCard(
                        title="Caption Video",
                        main_idea="Captions fast path avoids downloading full videos",
                        summary="Short summary",
                        category="Dev",
                        tags=["youtube", "captions"],
                        tools=[],
                        technologies=[],
                        key_ideas=["Fast captions"],
                        practical_takeaways=[],
                        usefulness_signals=UsefulnessSignals(
                            actionability=4,
                            novelty=4,
                            specificity=4,
                            reuse_likelihood=4,
                            personal_fit=4,
                        ),
                        usefulness_score=8,
                        usefulness_reason="Helpful",
                    ),
                    tokens_in=10,
                    tokens_out=20,
                )
            ),
        )
        app_cfg = {
            "analysis_depth": "compact",
            "auto_moments": True,
            "auto_opinion": False,
            "profile": {},
        }
        monkeypatch.setattr(processing_mod, "get_app_settings", AsyncMock(return_value=app_cfg))
        monkeypatch.setattr("app.services.key_pool.get_app_settings", AsyncMock(return_value=app_cfg))

        async with factory() as session:
            res = await processing_mod.process_material(session, mid)
            assert res.processing_status == ProcessingStatus.DONE.value
            assert resolve_spy.await_count == 0
            # Also verify M1.1: newly attached tags made it into the persisted embedding!
            assert isinstance(res.embedding, dict)
            assert res.embedding.get("model") == LOCAL_EMBED_MODEL
            vec_with = local_embed_text(
                material_embedding_text(res, category_name="Dev", tag_names=["youtube", "captions"])
            )
            vec_without_tags = local_embed_text(
                material_embedding_text(res, category_name="Dev", tag_names=[])
            )
            assert res.embedding["vec"] == vec_with
            assert res.embedding["vec"] != vec_without_tags
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# H7: Whisper DB rotation + AsyncGroq cleanup + isolated key_pool commits
# ---------------------------------------------------------------------------


async def test_h7_pooled_transcribe_rotates_and_closes_clients(tmp_path: Path, monkeypatch):
    engine, factory = await _make_session_factory(tmp_path, "h7_whisper.db")
    try:
        async with factory() as session:
            await key_pool_mod.add_keys(
                session,
                "groq",
                "gsk_first_rate_limited_key\ngsk_second_healthy_key_ok",
                label="k",
            )

        closed_keys: list[str] = []
        attempted_keys: list[str] = []

        from app.ai import RateLimitError

        class _FakeGroqTranscribe:
            def __init__(self, settings, api_key: str, key_id=None):
                self.api_key = api_key
                self.key_id = key_id

            async def transcribe(self, file_path: str, language=None):
                attempted_keys.append(self.api_key)
                if self.api_key == "gsk_first_rate_limited_key":
                    raise RateLimitError("429 RateLimitError: too many requests")
                return "transcribed ok", 42.0

            async def aclose(self):
                closed_keys.append(self.api_key)

        monkeypatch.setattr("app.ai.GroqTranscribeClient", _FakeGroqTranscribe)
        monkeypatch.setattr("app.db.SessionLocal", factory)

        fake_settings = Settings(groq_api_key="", google_ai_api_key="")
        async with factory() as session:
            client = PooledTranscribeClient(fake_settings, session=session)
            text_out, dur = await client.transcribe("/tmp/fake.mp3")
            assert text_out == "transcribed ok"
            assert dur == 42.0
            assert len(attempted_keys) == 2
            assert set(closed_keys) == set(attempted_keys)
    finally:
        await engine.dispose()


async def test_h7_key_pool_does_not_prematurely_commit_caller_material_mutations(
    tmp_path: Path, monkeypatch
):
    engine, factory = await _make_session_factory(tmp_path, "h7_isolation.db")
    try:
        monkeypatch.setattr("app.db.SessionLocal", factory)
        async with factory() as session:
            await key_pool_mod.add_keys(session, "groq", "gsk_iso_test_key_1234567890", label="iso")
            m = Material(source_type="note", title="Original Title", processing_status="queued")
            session.add(m)
            await session.commit()
            mid = m.id

        # Caller modifies Material in an uncommitted transaction, calls acquire_keys + mark_key_result,
        # then rolls back. The uncommitted title change MUST NOT be persisted!
        async with factory() as session:
            m = await session.get(Material, mid)
            m.title = "Uncommitted Dirty Title"
            keys = await key_pool_mod.acquire_keys(session, "groq")
            assert keys
            await key_pool_mod.mark_key_result(session, keys[0].id, ok=True)
            await session.rollback()

        async with factory() as verify_session:
            m_after = await verify_session.get(Material, mid)
            assert m_after.title == "Original Title"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# M1: Lightweight semantic search + provider mismatch fallback + reindex + graph light
# ---------------------------------------------------------------------------


async def test_m1_semantic_search_handles_provider_mismatch_and_graph_light(tmp_path: Path):
    engine, factory = await _make_session_factory(tmp_path, "m1.db")
    try:
        graph_mod.invalidate_graph_cache()
        async with factory() as session:
            cat = Category(name="AI")
            session.add(cat)
            await session.flush()

            m1 = Material(
                source_type="note",
                title="Vector databases and embeddings",
                summary="Hybrid semantic retrieval with cosine similarity",
                raw_text="vector search cosine similarity embeddings",
                category_id=cat.id,
                processing_status=ProcessingStatus.DONE.value,
                # Simulate stale embedding from a different provider/model
                embedding={"model": "google:text-embedding-004", "dim": 3, "vec": [0.1, 0.2, 0.3]},
            )
            m2 = Material(
                source_type="note",
                title="Semantic retrieval ranking",
                summary="Hybrid semantic retrieval with cosine similarity",
                raw_text="vector search cosine similarity embeddings",
                category_id=cat.id,
                processing_status=ProcessingStatus.DONE.value,
                embedding=await compute_embedding("Semantic retrieval ranking cosine similarity"),
            )
            session.add_all([m1, m2])
            await session.commit()

        # Semantic search still finds m1 despite mismatched stored model (via local fallback)
        async with factory() as session:
            hits = await _semantic_candidates(session, "vector databases cosine similarity", limit=5)
            hit_ids = set(hits.keys())
            assert m1.id in hit_ids
            assert m2.id in hit_ids

            # Reindex updates the mismatched embedding to LOCAL_EMBED_MODEL
            stats = await reindex_embeddings(session, force=False)
            assert stats["updated"] == 1
            assert stats["skipped"] == 1

            # Graph light mode skips semantic edges; full mode computes them
            g_light = await graph_mod.build_graph(session, include_semantic=False)
            assert not any(e.get("reason") == "semantic" for e in g_light["links"])

            g_full = await graph_mod.build_graph(session, include_semantic=True)
            assert any(e.get("reason") == "semantic" for e in g_full["links"])
    finally:
        graph_mod.invalidate_graph_cache()
        await engine.dispose()


# ---------------------------------------------------------------------------
# M2: delete_material cleans _atlas & isolated per-download dir + tts cache bound
# ---------------------------------------------------------------------------


async def test_m2_delete_material_cleans_atlas_and_isolated_dir_and_tts_cache_prunes(
    tmp_path: Path, monkeypatch
):
    engine, factory = await _make_session_factory(tmp_path, "m2.db")
    storage_root = tmp_path / "storage"
    fake_settings = Settings(storage_path=str(storage_root))
    monkeypatch.setattr("app.config.get_settings", lambda: fake_settings)

    # Create isolated TikTok carousel dir + _atlas dir + materials/<id> dir
    run_dir = storage_root / "tiktok" / "abcdef123456"
    run_dir.mkdir(parents=True)
    main_vid = run_dir / "slide_1.mp4"
    extra_vid = run_dir / "slide_2.mp4"
    atlas_dir = run_dir / "slide_1_atlas"
    atlas_dir.mkdir()
    main_vid.write_bytes(b"vid1")
    extra_vid.write_bytes(b"vid2")
    (atlas_dir / "frame_001.jpg").write_bytes(b"jpg")

    try:
        async with factory() as session:
            m = Material(
                source_type="tiktok",
                title="Carousel",
                file_path=str(main_vid),
                processing_status=ProcessingStatus.DONE.value,
            )
            session.add(m)
            await session.commit()
            mid = m.id

            mat_dir = storage_root / "materials" / mid
            mat_dir.mkdir(parents=True)
            (mat_dir / "moment.jpg").write_bytes(b"m")

            res = await delete_mod.delete_materials(session, [mid])
            assert res["deleted"] == [mid]
            assert res["missing"] == []

        assert not main_vid.exists()
        assert not extra_vid.exists()
        assert not atlas_dir.exists()
        assert not run_dir.exists()
        assert not (storage_root / "materials" / mid).exists()

        # TTS cache pruning bounds both file count and total bytes
        cache_dir = storage_root / "tts_cache"
        cache_dir.mkdir(parents=True)
        for i in range(6):
            p = cache_dir / f"sample_{i}.mp3"
            p.write_bytes(b"0" * 100)
            os.utime(p, (1000 + i * 10, 1000 + i * 10))
        removed = tts_mod.prune_tts_cache(cache_dir, max_files=3, max_bytes=10_000, max_age_seconds=0)
        assert removed == 3
        remaining = sorted(p.name for p in cache_dir.glob("*.mp3"))
        assert remaining == ["sample_3.mp3", "sample_4.mp3", "sample_5.mp3"]
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# M3: pdf.py OCR uses lang="rus+eng"
# ---------------------------------------------------------------------------


async def test_m3_pdf_ocr_passes_rus_eng_language(tmp_path: Path, monkeypatch):
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")

    class _FakePage:
        def extract_text(self):
            return ""

    class _FakeReader:
        def __init__(self, _path):
            self.pages = [_FakePage()]

    captured_langs: list[str | None] = []
    fake_pdf2image = types.ModuleType("pdf2image")
    fake_pdf2image.convert_from_path = lambda _p, dpi=200: ["fake_img"]
    fake_pytesseract = types.ModuleType("pytesseract")

    def _fake_ocr(_img, lang=None):
        captured_langs.append(lang)
        return "Распознанный русский текст"

    fake_pytesseract.image_to_string = _fake_ocr

    monkeypatch.setattr(pdf_mod, "PdfReader", _FakeReader)
    monkeypatch.setitem(sys.modules, "pdf2image", fake_pdf2image)
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)

    out = await pdf_mod.extract_pdf_text(str(fake_pdf))
    assert "Распознанный русский текст" in out
    assert captured_langs == ["rus+eng"]
