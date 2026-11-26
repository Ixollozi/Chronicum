"""In-process pacing for Groq Whisper / LLM under parallel lite jobs."""

from __future__ import annotations

import asyncio
import time

from app.logging_setup import get_logger

log = get_logger(__name__)

# Cap concurrent provider calls so parallel materials don't stampede one key.
_whisper_sem: asyncio.Semaphore | None = None
_llm_sem: asyncio.Semaphore | None = None

WHISPER_CONCURRENCY = 1
LLM_CONCURRENCY = 2
DEFAULT_COOLDOWN_SEC = 45.0

# key_id (int|str) → monotonic deadline
_cooldowns: dict[int | str, float] = {}


def _whisper_semaphore() -> asyncio.Semaphore:
    global _whisper_sem
    if _whisper_sem is None:
        _whisper_sem = asyncio.Semaphore(WHISPER_CONCURRENCY)
    return _whisper_sem


def _llm_semaphore() -> asyncio.Semaphore:
    global _llm_sem
    if _llm_sem is None:
        _llm_sem = asyncio.Semaphore(LLM_CONCURRENCY)
    return _llm_sem


def reset_limits_for_tests() -> None:
    global _whisper_sem, _llm_sem, _cooldowns
    _whisper_sem = None
    _llm_sem = None
    _cooldowns = {}


def set_key_cooldown(key_id: int | str, seconds: float = DEFAULT_COOLDOWN_SEC) -> None:
    until = time.monotonic() + max(1.0, seconds)
    _cooldowns[key_id] = until
    log.info("key_cooldown", key_id=str(key_id), seconds=seconds)


def clear_key_cooldown(key_id: int | str) -> None:
    _cooldowns.pop(key_id, None)


def key_in_cooldown(key_id: int | str) -> bool:
    until = _cooldowns.get(key_id)
    if until is None:
        return False
    if time.monotonic() >= until:
        _cooldowns.pop(key_id, None)
        return False
    return True


def is_rate_limit_error(error: str | None) -> bool:
    msg = (error or "").lower()
    return any(tok in msg for tok in ("429", "rate limit", "rate_limit", "quota", "too many requests"))


async def with_whisper_slot(coro):
    async with _whisper_semaphore():
        return await coro


async def with_llm_slot(coro):
    async with _llm_semaphore():
        return await coro
