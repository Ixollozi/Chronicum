"""Lite queue: parallel jobs with video/job semaphores (not a global lock)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.mark.asyncio
async def test_video_semaphore_allows_n_parallel(monkeypatch):
    from app.config import get_settings
    from app.services import queue as q

    monkeypatch.setenv("VIDEO_CONCURRENCY", "3")
    get_settings.cache_clear()
    q._reset_semaphores_for_tests()

    peak = {"n": 0}
    active = {"n": 0}
    sem = q._video_semaphore()

    async def grab():
        async with sem:
            active["n"] += 1
            peak["n"] = max(peak["n"], active["n"])
            await asyncio.sleep(0.05)
            active["n"] -= 1

    await asyncio.gather(*(grab() for _ in range(6)))
    assert peak["n"] == 3

    get_settings.cache_clear()
    q._reset_semaphores_for_tests()


@pytest.mark.asyncio
async def test_video_semaphore_caps_at_two(monkeypatch):
    from app.config import get_settings
    from app.services import queue as q

    monkeypatch.setenv("VIDEO_CONCURRENCY", "2")
    get_settings.cache_clear()
    q._reset_semaphores_for_tests()

    peak = {"n": 0}
    active = {"n": 0}
    sem = q._video_semaphore()

    async def grab():
        async with sem:
            active["n"] += 1
            peak["n"] = max(peak["n"], active["n"])
            await asyncio.sleep(0.04)
            active["n"] -= 1

    await asyncio.gather(*(grab() for _ in range(5)))
    assert peak["n"] == 2

    get_settings.cache_clear()
    q._reset_semaphores_for_tests()


@pytest.mark.asyncio
async def test_articles_not_blocked_by_full_video_slots(monkeypatch):
    """Articles take job slots only — still start while videos hold all video slots."""
    from app.config import get_settings
    from app.services import queue as q

    monkeypatch.setenv("JOB_CONCURRENCY", "4")
    monkeypatch.setenv("VIDEO_CONCURRENCY", "1")
    get_settings.cache_clear()
    q._reset_semaphores_for_tests()

    article_started = asyncio.Event()
    video_holding = asyncio.Event()
    release_video = asyncio.Event()

    async def hold_video():
        async with q._job_semaphore():
            async with q._video_semaphore():
                video_holding.set()
                await release_video.wait()

    async def run_article():
        async with q._job_semaphore():
            article_started.set()
            await asyncio.sleep(0.01)

    vt = asyncio.create_task(hold_video())
    await video_holding.wait()
    at = asyncio.create_task(run_article())
    await asyncio.wait_for(article_started.wait(), timeout=1.0)
    release_video.set()
    await asyncio.gather(vt, at)

    get_settings.cache_clear()
    q._reset_semaphores_for_tests()


def test_is_video_source():
    from app.services.queue import is_video_source

    assert is_video_source("youtube")
    assert is_video_source("instagram")
    assert not is_video_source("article")
    assert not is_video_source("note")
