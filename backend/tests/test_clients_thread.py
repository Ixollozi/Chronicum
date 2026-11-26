"""Unit tests for living client thread helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
async def session(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base

    db = tmp_path / "t.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s


@pytest.mark.asyncio
async def test_client_resolve_and_append(session):
    from app.services import clients as clients_svc

    c = await clients_svc.create_client(
        session,
        display_name="Иван Петров",
        aliases=["Ваня", "Иван"],
        context_line="Лид по Chronicum",
    )
    assert c.id
    found = await clients_svc.resolve_client(session, "Ваня")
    assert found and found.id == c.id
    c2, msg = await clients_svc.append_message(session, c.id, "Созвон в пятницу", source="web")
    assert msg.body.startswith("Созвон")
    assert len(c2.messages) == 1
    pack = await clients_svc.client_context_pack(session, "Иван", n=5)
    assert pack["client"]["display_name"] == "Иван Петров"
    assert len(pack["recent_messages"]) == 1
    assert pack["client"]["summary_stale"] is True


@pytest.mark.asyncio
async def test_ambiguous_resolve_and_dedup(session):
    from app.services import clients as clients_svc

    await clients_svc.create_client(session, display_name="Иван Петров", aliases=["Иван"], force=True)
    await clients_svc.create_client(session, display_name="Иван Сидоров", aliases=["Иван"], force=True)

    res = await clients_svc.resolve_client_v2(session, "Иван")
    assert res["status"] == "ambiguous"
    assert len(res["candidates"]) == 2
    with pytest.raises(clients_svc.AmbiguousClientError):
        await clients_svc.client_context_pack(session, "Иван")

    with pytest.raises(clients_svc.DuplicateClientError):
        await clients_svc.create_client(session, display_name="Иван Петров", aliases=["Иван"])


@pytest.mark.asyncio
async def test_paste_commit_and_merge(session):
    from app.services import clients as clients_svc

    c = await clients_svc.create_client(session, display_name="BOVARI MARKETING", aliases=["BOVARI"])
    text = (
        "[12.03.2026, 14:02] BOVARI MARKETING: Привет, по оплате в пятницу\n"
        "[12.03.2026, 14:05] Самир: Ок, пришлю счёт\n"
    )
    c, added, skipped = await clients_svc.commit_paste(
        session, c.id, text, speaker_roles={"BOVARI MARKETING": "client", "Самир": "me"}
    )
    assert added == 2 and skipped == 0
    assert c.messages[0].speaker == "client"
    assert c.messages[0].event_at is not None
    _c, added2, skipped2 = await clients_svc.commit_paste(session, c.id, text)
    assert added2 == 0 and skipped2 == 2

    dup = await clients_svc.create_client(session, display_name="BOVARI 2", force=True)
    await clients_svc.append_message(session, dup.id, "ещё факт", source="mcp")
    merged = await clients_svc.merge_clients(session, dup.id, c.id)
    assert len(merged.messages) == 3
