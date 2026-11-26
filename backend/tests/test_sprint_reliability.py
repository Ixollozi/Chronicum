"""Sprint: reclaim, AI pacing, URL dedup, batch paste, moments gate."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.mark.asyncio
async def test_reclaim_stale_jobs_resets_processing():
    from app.models import ProcessingStatus
    from app.services.queue import reclaim_stale_jobs

    stuck = SimpleNamespace(
        id="stuck-1",
        processing_status=ProcessingStatus.PROCESSING.value,
    )
    queued = SimpleNamespace(
        id="queued-1",
        processing_status=ProcessingStatus.QUEUED.value,
    )

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _Scalars(self._rows)

    calls = {"n": 0}

    class _Session:
        async def execute(self, _stmt):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Result([stuck])
            return _Result([stuck.id, queued.id])

        async def commit(self):
            return None

    ids = await reclaim_stale_jobs(_Session())
    assert stuck.processing_status == ProcessingStatus.QUEUED.value
    assert "stuck-1" in ids
    assert "queued-1" in ids


@pytest.mark.asyncio
async def test_whisper_semaphore_serializes():
    from app.services import ai_limits as lim

    lim.reset_limits_for_tests()
    peak = {"n": 0}
    active = {"n": 0}

    async def grab():
        async def _inner():
            active["n"] += 1
            peak["n"] = max(peak["n"], active["n"])
            await asyncio.sleep(0.04)
            active["n"] -= 1
            return True

        return await lim.with_whisper_slot(_inner())

    await asyncio.gather(grab(), grab(), grab())
    assert peak["n"] == 1
    lim.reset_limits_for_tests()


@pytest.mark.asyncio
async def test_llm_semaphore_caps_at_two():
    from app.services import ai_limits as lim

    lim.reset_limits_for_tests()
    peak = {"n": 0}
    active = {"n": 0}

    async def grab():
        async def _inner():
            active["n"] += 1
            peak["n"] = max(peak["n"], active["n"])
            await asyncio.sleep(0.04)
            active["n"] -= 1
            return True

        return await lim.with_llm_slot(_inner())

    await asyncio.gather(*(grab() for _ in range(5)))
    assert peak["n"] == 2
    lim.reset_limits_for_tests()


def test_key_cooldown_skips_after_429():
    from app.services import ai_limits as lim

    lim.reset_limits_for_tests()
    lim.set_key_cooldown("env:groq", seconds=30)
    assert lim.key_in_cooldown("env:groq")
    assert lim.key_cooldown_remaining("env:groq") > 0
    assert lim.is_rate_limit_error("Error 429 rate_limit exceeded")
    lim.clear_key_cooldown("env:groq")
    assert not lim.key_in_cooldown("env:groq")
    lim.reset_limits_for_tests()


def test_parse_retry_after_and_transient_classification():
    from app.services import ai_limits as lim

    groq_429 = (
        "Error code: 429 - Rate limit reached for model openai/gpt-oss-120b "
        "on tokens per minute (TPM): Limit 8000, Used 6193. Please try again in 11.54s."
    )
    assert 12.0 <= lim.parse_retry_after_seconds(groq_429) <= 13.0
    assert lim.is_transient_rate_limit_error(groq_429) is True

    assert lim.parse_retry_after_seconds("Please try again in 1m5s") == 65.0
    assert lim.is_transient_rate_limit_error("Error code: 401 - invalid api key") is False
    assert (
        lim.is_transient_rate_limit_error(
            "Error code: 429 - Rate limit reached on tokens per day (TPD)"
        )
        is False
    )


async def test_with_llm_fallback_waits_out_short_tpm_429(monkeypatch):
    import asyncio
    from app import ai as app_ai
    from app.services import ai_limits as lim
    from app.services import key_pool as kp

    lim.reset_limits_for_tests()
    sleeps: list[float] = []
    orig_sleep = asyncio.sleep

    async def fast_sleep(sec: float):
        sleeps.append(sec)
        # Clear cooldown to simulate time passing without slowing the test suite
        lim.clear_key_cooldown("env:groq")
        await orig_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    fake_key = kp.PooledKey(
        id="env:groq", provider="groq", api_key="gsk_test", label=".env", source="env"
    )

    async def fake_acquire(_session, provider: str):
        return [fake_key] if provider == "groq" else []

    async def fake_mark(_session, key_id, *, ok: bool, error: str | None = None):
        if ok:
            lim.clear_key_cooldown(key_id)
        elif lim.is_rate_limit_error(error):
            lim.set_key_cooldown(key_id, seconds=lim.parse_retry_after_seconds(error))

    class DummyClient:
        async def aclose(self):
            return None

    monkeypatch.setattr(kp, "acquire_keys", fake_acquire)
    monkeypatch.setattr(kp, "mark_key_result", fake_mark)
    monkeypatch.setattr(app_ai, "_client_for", lambda *a, **kw: DummyClient())
    monkeypatch.setattr(
        app_ai, "get_settings", lambda: SimpleNamespace(ai_provider="groq")
    )

    calls = {"n": 0}

    async def flaky_fn(_client):
        calls["n"] += 1
        if calls["n"] == 1:
            raise app_ai.RateLimitError(
                "Error code: 429 - tokens per minute (TPM). Please try again in 8.2s."
            )
        return "ok-after-wait"

    res = await app_ai.with_llm_fallback(None, flaky_fn)
    assert res == "ok-after-wait"
    assert calls["n"] == 2
    assert len(sleeps) == 1
    assert 8.5 <= sleeps[0] <= 10.0

    # Auth 401 error must fail fast without sleeping
    sleeps.clear()

    async def auth_fail_fn(_client):
        raise app_ai.RateLimitError("Error code: 401 - invalid api key")

    try:
        await app_ai.with_llm_fallback(None, auth_fail_fn)
        assert False, "expected RuntimeError on 401"
    except RuntimeError as e:
        assert "All API keys exhausted" in str(e)
    assert sleeps == []
    lim.reset_limits_for_tests()


def test_instagram_canonical_url_collapses_variants():
    from app.services.storage import url_sha256

    a = url_sha256("https://www.instagram.com/reel/AbCdEfGhIjK/?igsh=xyz")
    b = url_sha256("https://www.instagram.com/p/AbCdEfGhIjK/")
    c = url_sha256("https://instagr.am/tv/AbCdEfGhIjK")
    assert a == b == c
    assert url_sha256("https://www.instagram.com/reel/OtherCode99/") != a


def test_needs_enqueue_rules():
    from app.models import ProcessingStatus
    from app.services.processing import needs_enqueue

    done = SimpleNamespace(processing_status=ProcessingStatus.DONE.value, summary="ok")
    failed = SimpleNamespace(processing_status=ProcessingStatus.FAILED.value, summary=None)
    processing = SimpleNamespace(processing_status=ProcessingStatus.PROCESSING.value, summary=None)
    queued = SimpleNamespace(processing_status=ProcessingStatus.QUEUED.value, summary=None)

    assert needs_enqueue(done, already_exists=True) is False
    assert needs_enqueue(failed, already_exists=True) is True
    assert needs_enqueue(processing, already_exists=True) is False
    assert needs_enqueue(queued, already_exists=False) is True


def test_extract_urls_unique_by_canonical():
    from app.services.composer import extract_urls

    text = (
        "https://www.instagram.com/reel/AbCdEfGhIjK/?igsh=1\n"
        "https://www.instagram.com/p/AbCdEfGhIjK/\n"
        "https://youtu.be/dQw4w9wgGcQ\n"
        "https://www.youtube.com/watch?v=dQw4w9wgGcQ&t=10"
    )
    urls = extract_urls(text)
    assert len(urls) == 2


def test_auto_moments_default_false():
    from app.services.key_pool import SETTINGS_DEFAULTS

    assert SETTINGS_DEFAULTS["auto_moments"] == "false"


def test_moments_weak_evidence_gate_condition():
    """Mirror the gate predicate used in build_key_moments."""
    from app.services.resources import speech_unreliable

    weak_raw = "[0:00] Спасибо за просмотр!\n\n[On-screen OCR timed]\n"
    assert speech_unreliable(weak_raw) is True
    listicle = False
    usable_ocr_n = 1
    overlay_n = 0
    should_skip = (
        not listicle
        and speech_unreliable(weak_raw)
        and usable_ocr_n < 3
        and overlay_n < 2
    )
    assert should_skip is True
