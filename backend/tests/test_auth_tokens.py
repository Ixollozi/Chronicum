from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import User, UserApiToken
from app.services.auth import (
    LAST_USED_TOUCH_INTERVAL,
    decode_session_jwt,
    hash_api_token,
    issue_session_jwt,
    lookup_api_token,
    mint_api_token_raw,
)


def test_pat_hash_stable():
    raw = mint_api_token_raw()
    assert raw.startswith("chr_")
    assert hash_api_token(raw) == hash_api_token(raw)
    assert hash_api_token(raw) != hash_api_token(raw + "x")


def test_session_jwt_roundtrip():
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        google_sub="sub",
        email="a@b.c",
        name="Ada",
        picture_url=None,
    )
    token = issue_session_jwt(user)
    payload = decode_session_jwt(token)
    assert payload is not None
    assert payload["sub"] == user.id
    assert payload["email"] == "a@b.c"
    assert decode_session_jwt("not-a-jwt") is None


@pytest.mark.asyncio
async def test_lookup_api_token_throttles_last_used_at_and_handles_sqlite_lock():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    raw = mint_api_token_raw()
    async with Session() as s:
        u = User(
            id="22222222-2222-2222-2222-222222222222",
            google_sub="sub-throttle",
            email="throttle@test.local",
            name="Throttle",
        )
        t = UserApiToken(
            user_id=u.id,
            label="cli",
            token_hash=hash_api_token(raw),
            token_prefix=raw[:12],
            last_used_at=None,
        )
        s.add_all([u, t])
        await s.commit()

    # 1. First lookup: last_used_at is None -> commits update once
    async with Session() as s:
        commits = 0
        orig_commit = s.commit

        async def counting_commit():
            nonlocal commits
            commits += 1
            return await orig_commit()

        s.commit = counting_commit  # type: ignore[method-assign]
        res = await lookup_api_token(s, raw)
        assert res is not None
        user, tok = res
        assert user.id == "22222222-2222-2222-2222-222222222222"
        assert tok.last_used_at is not None
        assert commits == 1

    # 2. Immediate second lookup (< 5 min): MUST skip commit
    async with Session() as s:
        commits = 0
        orig_commit = s.commit

        async def counting_commit_2():
            nonlocal commits
            commits += 1
            return await orig_commit()

        s.commit = counting_commit_2  # type: ignore[method-assign]
        res = await lookup_api_token(s, raw)
        assert res is not None
        assert commits == 0

    # 3. Stale last_used_at (> 5 min) + transient OperationalError on commit:
    #    must roll back gracefully and still return valid (user, token)
    async with Session() as s:
        res_pre = await lookup_api_token(s, raw)
        assert res_pre is not None
        _, tok_pre = res_pre
        tok_pre.last_used_at = datetime.now(timezone.utc) - LAST_USED_TOUCH_INTERVAL - timedelta(seconds=30)
        await s.commit()

    async with Session() as s:
        rollback_called = False
        orig_rollback = s.rollback

        async def failing_commit():
            raise OperationalError("UPDATE api_tokens ...", {}, Exception("database is locked"))

        async def tracked_rollback():
            nonlocal rollback_called
            rollback_called = True
            return await orig_rollback()

        s.commit = failing_commit  # type: ignore[method-assign]
        s.rollback = tracked_rollback  # type: ignore[method-assign]
        res = await lookup_api_token(s, raw)
        assert res is not None
        user, tok = res
        assert rollback_called is True
        assert user.id == "22222222-2222-2222-2222-222222222222"
        assert user.email == "throttle@test.local"
        assert tok.token_prefix == raw[:12]

    await engine.dispose()
