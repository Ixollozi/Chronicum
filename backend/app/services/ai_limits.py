"""In-process pacing for Groq Whisper / LLM under parallel lite jobs."""

from __future__ import annotations

import asyncio
import re
import time

from app.logging_setup import get_logger

log = get_logger(__name__)

# Cap concurrent provider calls so parallel materials don't stampede one key.
_whisper_sem: asyncio.Semaphore | None = None
_llm_sem: asyncio.Semaphore | None = None

WHISPER_CONCURRENCY = 1
LLM_CONCURRENCY = 2
DEFAULT_COOLDOWN_SEC = 45.0
MAX_PARSED_COOLDOWN_SEC = 65.0

# key_id (int|str) → monotonic deadline
_cooldowns: dict[int | str, float] = {}

_RETRY_MIN_SEC_RE = re.compile(
    r"(?:try again in|retry[- ]?after[:=\s]+)\s*(?:(\d+)\s*m\s*)?(\d+(?:\.\d+)?)\s*(ms|s|sec|seconds?)?",
    re.IGNORECASE,
)


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
    log.info("key_cooldown", key_id=str(key_id), seconds=round(max(1.0, seconds), 2))


def clear_key_cooldown(key_id: int | str) -> None:
    _cooldowns.pop(key_id, None)


def key_cooldown_remaining(key_id: int | str) -> float:
    until = _cooldowns.get(key_id)
    if until is None:
        return 0.0
    remaining = until - time.monotonic()
    if remaining <= 0:
        _cooldowns.pop(key_id, None)
        return 0.0
    return remaining


def key_in_cooldown(key_id: int | str) -> bool:
    return key_cooldown_remaining(key_id) > 0.0


def is_rate_limit_error(error: str | None) -> bool:
    msg = (error or "").lower()
    return any(tok in msg for tok in ("429", "rate limit", "rate_limit", "quota", "too many requests"))


def is_transient_rate_limit_error(error: str | None) -> bool:
    """True for short-lived TPM/RPM 429s worth waiting out; False for 401/auth, 413 payload size, or daily TPD limits."""
    msg = (error or "").lower()
    if not is_rate_limit_error(msg):
        return False
    if any(
        tok in msg
        for tok in (
            "413",
            "request too large",
            "payload too large",
            "entity too large",
            "401",
            "invalid api key",
            "invalid_api_key",
            "unauthorized",
            "tokens per day",
            "(tpd)",
            "requests per day",
            "(rpd)",
            "billing",
            "insufficient_quota",
        )
    ):
        return False
    return True


def parse_retry_after_seconds(error: str | None, default: float = DEFAULT_COOLDOWN_SEC) -> float:
    """Extract retry delay from provider 429 message (e.g. Groq 'Please try again in 11.54s')."""
    if not error:
        return default
    m = _RETRY_MIN_SEC_RE.search(error)
    if not m:
        return default
    mins = float(m.group(1) or 0.0)
    val = float(m.group(2) or 0.0)
    unit = (m.group(3) or "s").lower()
    total = mins * 60.0 + (val / 1000.0 if unit == "ms" else val)
    if total <= 0:
        return default
    return min(MAX_PARSED_COOLDOWN_SEC, max(1.0, total + 1.0))


async def with_whisper_slot(coro):
    async with _whisper_semaphore():
        return await coro


async def with_llm_slot(coro):
    async with _llm_semaphore():
        return await coro
