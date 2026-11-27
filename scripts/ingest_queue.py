#!/usr/bin/env python3
"""Continuous multi-slot batch URL ingestion queue for local Chronicum.

Runs `--concurrency` independent worker slots (default: 2) pulling one URL at a
time from a shared queue as soon as each slot finishes. Handles HTTP 429 and
in-material rate-limit errors (Groq/Gemini) with a shared cross-slot cooldown,
`/api/materials/{id}/reprocess?only=full` retries (up to 2 per URL), and a
circuit breaker (`STOPPED_RATE_LIMIT`) after 3+ consecutive rate-limit failures
across distinct URLs so remaining pending items stay untouched for resume.

Uses only the Python standard library so it works in any Python 3.10+ runtime.
Never logs or prints the API bearer token.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import random
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE_FILE = REPO_ROOT / "data" / "ingest_queue.txt"
DEFAULT_STATE_FILE = REPO_ROOT / "data" / "ingest_queue_state.json"
DEFAULT_LOG_FILE = REPO_ROOT / "data" / "ingest_queue.log"
DEFAULT_LOCK_FILE = REPO_ROOT / "data" / "ingest_queue.lock"
DEFAULT_API_URL = "http://127.0.0.1:8010"

DEFAULT_CONCURRENCY = 2
DEFAULT_STAGGER_RANGE = (2.0, 4.0)
INSTAGRAM_DELAY_RANGE = (8.0, 14.0)
OTHER_DELAY_RANGE = (4.0, 6.0)
DEFAULT_HTTP_TIMEOUT = 45.0
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_BACKPRESSURE_INTERVAL = 5.0
DEFAULT_HEARTBEAT_SEC = 45.0
DEFAULT_RATE_LIMIT_WAIT = 60.0
DEFAULT_RATE_LIMIT_RETRIES = 2
DEFAULT_MAX_CONSECUTIVE_RATE_LIMITS = 3
MAX_PARSED_COOLDOWN_SEC = 600.0

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)
TIKTOK_POST_RE = re.compile(r"tiktok\.com/@[^/]+/(?:video|photo)/(\d+)", re.IGNORECASE)
IG_SHORTCODE_RE = re.compile(
    r"(?:instagram\.com|instagr\.am)/(?:reels?|p|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
RATE_LIMIT_RE = re.compile(
    r"(?:"
    r"\b429\b|"
    r"rate[\s_-]*limit|"
    r"too\s+many\s+requests|"
    r"resource[\s_-]*exhausted|"
    r"\bquota\b|"
    r"\btpm\b|"
    r"\brpm\b|"
    r"\brpd\b|"
    r"\btpd\b|"
    r"try\s+again\s+in|"
    r"retry[\s_-]*after|"
    r"лимит"
    r")",
    re.IGNORECASE,
)
RETRY_DURATION_RE = re.compile(
    r"(?:try\s+again\s+in|retry[\s_-]*after[:=\s]+|повторите\s+через|через)\s*"
    r"(?:(\d+)\s*(?:h|ч)\s*)?"
    r"(?:(\d+)\s*(?:m|min|мин)\s*)?"
    r"(\d+(?:\.\d+)?)\s*(ms|мс|s|sec|seconds?|сек|m|min|мин)?",
    re.IGNORECASE,
)

TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "igsh",
        "igshid",
        "mc_cid",
        "mc_eid",
        "si",
        "utm_campaign",
        "utm_content",
        "utm_id",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
)


class QueueAlreadyRunningError(RuntimeError):
    """Raised when another ingest_queue.py process holds the lock."""


class AuthError(RuntimeError):
    """Raised when Chronicum API rejects authentication (401/403)."""


class HttpApiError(RuntimeError):
    """HTTP error returned by Chronicum API."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _now_log_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_extracted_url(raw: str) -> str:
    url = raw.strip()
    while url and url[-1] in ".,;:!?\"'<>]}":
        url = url[:-1]
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    return url


def _is_valid_http_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = (parts.hostname or "").strip().lower()
    if not host:
        return False
    return "." in host or host == "localhost"


def canonical_url_key(url: str) -> str:
    """Canonical deduplication key aligned with Chronicum backend URL hashing."""
    raw = (url or "").strip()
    m = YOUTUBE_ID_RE.search(raw)
    if m:
        return f"youtube:{m.group(1)}"
    m = TIKTOK_POST_RE.search(raw)
    if m:
        return f"tiktok:{m.group(1)}"
    m = IG_SHORTCODE_RE.search(raw)
    if m:
        return f"instagram:{m.group(1)}"

    try:
        parts = urllib.parse.urlsplit(raw)
        path = (parts.path or "/").rstrip("/") or "/"
        kept_query = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in TRACKING_QUERY_KEYS and not k.lower().startswith("utm_")
        ]
        kept_query.sort()
        query = urllib.parse.urlencode(kept_query)
        return urllib.parse.urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), path, query, "")
        )
    except Exception:
        return raw.rstrip("/")


def is_instagram_url(url: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "instagram.com" or host.endswith(".instagram.com") or host == "instagr.am"


def extract_urls_from_text(text: str) -> list[str]:
    """Extract valid deduplicated HTTP(S) URLs from raw queue text."""
    urls: list[str] = []
    seen: set[str] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for match in URL_RE.finditer(stripped):
            candidate = _clean_extracted_url(match.group(0))
            if not _is_valid_http_url(candidate):
                continue
            key = canonical_url_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            urls.append(candidate)

    return urls


def load_queue_urls(queue_file: Path) -> list[str]:
    if not queue_file.exists():
        return []
    return extract_urls_from_text(queue_file.read_text(encoding="utf-8-sig"))


def resolve_api_token(mcp_config_path: Path | None = None) -> str | None:
    """Resolve Bearer token from CHRONICUM_API_TOKEN or ~/.cursor/mcp.json.

    Never prints or logs the token.
    """
    env_token = os.environ.get("CHRONICUM_API_TOKEN", "").strip()
    if env_token:
        if env_token.lower().startswith("bearer "):
            return env_token[7:].strip() or None
        return env_token

    cfg_path = mcp_config_path or (Path.home() / ".cursor" / "mcp.json")
    if not cfg_path.exists():
        return None
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None

    server = (data.get("mcpServers") or {}).get("chronicum") or {}
    headers = server.get("headers") or {}
    if not isinstance(headers, dict):
        return None
    for k, v in headers.items():
        if isinstance(k, str) and k.lower() == "authorization" and isinstance(v, str):
            val = v.strip()
            if val.lower().startswith("bearer "):
                val = val[7:].strip()
            return val or None
    return None


def resolve_api_url(cli_api_url: str | None = None) -> str:
    url = (cli_api_url or os.environ.get("CHRONICUM_API_URL") or DEFAULT_API_URL).strip()
    return url.rstrip("/")


def load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {"version": 1, "updated_at": None, "items": {}}
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
            return raw
    except Exception:
        pass
    return {"version": 1, "updated_at": None, "items": {}}


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state["version"] = 1
    state["updated_at"] = _now_iso()
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    tmp_path = state_file.with_suffix(
        state_file.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp"
    )
    tmp_path.write_text(payload, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp_path, state_file)
            return
        except PermissionError:
            if attempt == 4:
                tmp_path.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def get_item_state(state: dict[str, Any], url: str) -> dict[str, Any] | None:
    items = state.get("items") or {}
    key = canonical_url_key(url)
    entry = items.get(key)
    if isinstance(entry, dict):
        return entry
    raw_entry = items.get(url)
    return raw_entry if isinstance(raw_entry, dict) else None


def set_item_state(
    state: dict[str, Any],
    url: str,
    *,
    status: str,
    material_id: str | None = None,
    title: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    items = state.setdefault("items", {})
    key = canonical_url_key(url)
    prev = items.get(key) if isinstance(items.get(key), dict) else {}
    attempts = int(prev.get("attempts", 0)) + 1
    entry = {
        "url": url,
        "status": status,
        "material_id": material_id,
        "title": title,
        "error": error,
        "attempts": attempts,
        "updated_at": _now_iso(),
    }
    items[key] = entry
    return entry


@dataclass
class QueuePlan:
    total_urls: int
    to_process: list[str]
    done_count: int
    duplicate_count: int
    failed_count: int
    pending_count: int


def build_queue_plan(
    urls: list[str],
    state: dict[str, Any],
    *,
    retry_failed: bool = False,
    limit: int | None = None,
) -> QueuePlan:
    done_count = 0
    duplicate_count = 0
    failed_count = 0
    pending_count = 0
    selected: list[str] = []

    for url in urls:
        entry = get_item_state(state, url)
        st = (entry or {}).get("status")
        if st == "done":
            done_count += 1
        elif st == "duplicate":
            duplicate_count += 1
        elif st == "failed":
            failed_count += 1
            if retry_failed:
                selected.append(url)
        else:
            pending_count += 1
            if not retry_failed:
                selected.append(url)

    if limit is not None and limit >= 0:
        selected = selected[:limit]

    return QueuePlan(
        total_urls=len(urls),
        to_process=selected,
        done_count=done_count,
        duplicate_count=duplicate_count,
        failed_count=failed_count,
        pending_count=pending_count,
    )


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5 or kernel32.GetLastError() == 5
        try:
            exit_code = ctypes.c_ulong(0)
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class QueueLock:
    """PID lock file preventing concurrent queue workers."""

    def __init__(self, lock_file: Path) -> None:
        self.lock_file = lock_file
        self._acquired = False

    def acquire(self) -> None:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(
                    str(self.lock_file),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                try:
                    payload = json.dumps(
                        {"pid": os.getpid(), "started_at": _now_iso()},
                        ensure_ascii=False,
                    )
                    os.write(fd, payload.encode("utf-8"))
                finally:
                    os.close(fd)
                self._acquired = True
                return
            except FileExistsError:
                owner_pid = self._read_pid()
                if owner_pid is not None and _is_pid_running(owner_pid):
                    raise QueueAlreadyRunningError(
                        f"Queue already running (PID {owner_pid}, lock: {self.lock_file})"
                    )
                try:
                    self.lock_file.unlink()
                except FileNotFoundError:
                    pass
        raise QueueAlreadyRunningError(f"Could not acquire lock: {self.lock_file}")

    def _read_pid(self) -> int | None:
        try:
            raw = self.lock_file.read_text(encoding="utf-8").strip()
            if not raw:
                return None
            if raw.isdigit():
                return int(raw)
            data = json.loads(raw)
            pid = data.get("pid")
            return int(pid) if pid is not None else None
        except Exception:
            return None

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            if self._read_pid() == os.getpid():
                self.lock_file.unlink(missing_ok=True)
        except Exception:
            pass
        finally:
            self._acquired = False

    def __enter__(self) -> "QueueLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


class QueueLogger:
    """Thread-safe line logger writing to file and optionally stdout."""

    def __init__(self, log_file: Path, *, quiet: bool = False) -> None:
        self.log_file = log_file
        self.quiet = quiet
        self._lock = threading.Lock()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def info(self, message: str) -> None:
        line = f"[{_now_log_ts()}] {message}"
        with self._lock:
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
            if not self.quiet:
                print(line, flush=True)


def _parse_retry_after_header(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.strip()
    try:
        sec = float(raw)
        if sec > 0:
            return min(MAX_PARSED_COOLDOWN_SEC, sec)
    except ValueError:
        pass
    return None


class ChronicumClient:
    """Minimal stdlib thread-safe HTTP client for local Chronicum API."""

    def __init__(
        self,
        api_url: str,
        token: str,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._token = token
        self.timeout = max(3.0, float(timeout))

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Connection": "close",
        }
        data: bytes | None = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif method == "POST":
            data = b""

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        req_timeout = max(3.0, float(timeout)) if timeout is not None else self.timeout
        try:
            with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            detail = err_body
            try:
                parsed = json.loads(err_body)
                if isinstance(parsed, dict) and parsed.get("detail"):
                    detail = str(parsed["detail"])
            except Exception:
                pass
            detail = detail[:300] or exc.reason or "HTTP error"
            retry_after = (
                _parse_retry_after_header(exc.headers.get("Retry-After"))
                if exc.headers
                else None
            )
            if exc.code in (401, 403):
                raise AuthError(f"HTTP {exc.code}: {detail}") from exc
            raise HttpApiError(exc.code, detail, retry_after=retry_after) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise RuntimeError(f"HTTP timeout after {req_timeout:.0f}s") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Connection error: {exc.reason}") from exc

    def enqueue_url(self, url: str) -> dict[str, Any]:
        return self._request("POST", "/api/materials/url", {"url": url})

    def reprocess_material(self, material_id: str, *, only: str = "full") -> dict[str, Any]:
        mid = urllib.parse.quote(material_id, safe="")
        q = urllib.parse.urlencode({"only": only})
        return self._request("POST", f"/api/materials/{mid}/reprocess?{q}")

    def get_material(self, material_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/materials/{urllib.parse.quote(material_id, safe='')}",
            timeout=timeout,
        )

    def get_busy_count(self, *, timeout: float | None = None) -> int:
        try:
            data = self._request("GET", "/api/materials/busy", timeout=timeout)
            if "total" in data:
                return int(data["total"])
            return int(data.get("queued", 0)) + int(data.get("processing", 0))
        except HttpApiError as exc:
            if exc.status_code != 404:
                raise
            # Fallback for older backends without /materials/busy
            listing = self._request("GET", "/api/materials?limit=50", timeout=timeout)
            items = listing.get("items") or []
            return sum(
                1
                for it in items
                if str(it.get("processing_status") or "").lower() in ("queued", "processing")
            )

    def find_material_by_url(self, url: str, *, limit: int = 50) -> dict[str, Any] | None:
        target_key = canonical_url_key(url)
        q = urllib.parse.urlencode({"limit": limit, "sort": "newest"})
        listing = self._request("GET", f"/api/materials?{q}")
        for it in listing.get("items") or []:
            src = it.get("source_url") or ""
            if src and canonical_url_key(src) == target_key:
                return it
        return None


def is_rate_limit_error(exc_or_msg: Any) -> bool:
    """Detect HTTP 429 or Groq/Gemini/provider rate-limit error messages."""
    if isinstance(exc_or_msg, HttpApiError) and exc_or_msg.status_code == 429:
        return True
    return bool(RATE_LIMIT_RE.search(str(exc_or_msg or "")))


def parse_rate_limit_wait(
    exc_or_msg: Any,
    *,
    retry_index: int = 0,
    base_wait: float = DEFAULT_RATE_LIMIT_WAIT,
) -> float:
    """Extract wait duration from Retry-After header or error text; else escalate 60s -> 120s."""
    if isinstance(exc_or_msg, HttpApiError) and exc_or_msg.retry_after:
        return min(MAX_PARSED_COOLDOWN_SEC, max(1.0, exc_or_msg.retry_after + 1.0))

    text = str(exc_or_msg or "")
    m = RETRY_DURATION_RE.search(text)
    if m:
        hours = float(m.group(1) or 0.0)
        mins = float(m.group(2) or 0.0)
        val = float(m.group(3) or 0.0)
        unit = (m.group(4) or "s").lower()
        if unit in ("ms", "мс"):
            val_sec = val / 1000.0
        elif unit in ("m", "min", "мин") and not m.group(2):
            val_sec = val * 60.0
        else:
            val_sec = val
        total = hours * 3600.0 + mins * 60.0 + val_sec
        if total > 0:
            return min(MAX_PARSED_COOLDOWN_SEC, max(1.0, total + 2.0))

    scale = 2 ** max(0, retry_index)
    return min(MAX_PARSED_COOLDOWN_SEC, max(0.0, base_wait * scale))


def choose_delay(
    url: str,
    *,
    delay_min: float | None = None,
    delay_max: float | None = None,
    rng: Callable[[float, float], float] = random.uniform,
) -> float:
    if delay_min is not None or delay_max is not None:
        lo = delay_min if delay_min is not None else (delay_max or 0.0)
        hi = delay_max if delay_max is not None else lo
    elif is_instagram_url(url):
        lo, hi = INSTAGRAM_DELAY_RANGE
    else:
        lo, hi = OTHER_DELAY_RANGE
    if hi < lo:
        lo, hi = hi, lo
    lo = max(0.0, lo)
    hi = max(lo, hi)
    return rng(lo, hi)


def _interruptible_sleep(
    seconds: float,
    *,
    stop_event: threading.Event | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Sleep up to `seconds`, returning False early if stop_event is set."""
    if seconds <= 0:
        return not (stop_event and stop_event.is_set())
    if stop_event is not None and sleep_fn is time.sleep:
        return not stop_event.wait(seconds)
    sleep_fn(seconds)
    return not (stop_event and stop_event.is_set())


class SharedRateLimitGate:
    """Cross-slot cooldown + circuit breaker for 429 / exhausted API keys."""

    def __init__(
        self,
        *,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_RATE_LIMITS,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        logger: QueueLogger | None = None,
    ) -> None:
        self.max_consecutive_failures = max(1, int(max_consecutive_failures))
        self._monotonic = monotonic_fn
        self._sleep = sleep_fn
        self._logger = logger
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self.consecutive_rl_failures = 0
        self.stopped_rate_limit = False

    def trigger_cooldown(
        self,
        seconds: float,
        *,
        reason: str = "",
        source_tag: str = "",
    ) -> float:
        wait = max(0.0, float(seconds))
        now = self._monotonic()
        target = now + wait
        with self._lock:
            if target > self._cooldown_until:
                self._cooldown_until = target
            effective = max(0.0, self._cooldown_until - now)
        if self._logger:
            prefix = f"{source_tag} " if source_tag else ""
            why = f" reason={_short_text(reason, 120)}" if reason else ""
            self._logger.info(f"{prefix}RATE_LIMIT_COOLDOWN wait={effective:.1f}s{why}")
        return effective

    def wait_if_cooling(
        self,
        *,
        stop_event: threading.Event | None = None,
        source_tag: str = "",
    ) -> bool:
        """Pause caller until shared cooldown expires. Returns False if stopped."""
        logged = False
        while True:
            if stop_event and stop_event.is_set():
                return False
            with self._lock:
                remaining = self._cooldown_until - self._monotonic()
            if remaining <= 0:
                return True
            if self._logger and not logged:
                prefix = f"{source_tag} " if source_tag else ""
                self._logger.info(f"{prefix}COOLDOWN_WAIT {remaining:.1f}s")
                logged = True
            if not _interruptible_sleep(remaining, stop_event=stop_event, sleep_fn=self._sleep):
                return False

    def record_item_outcome(
        self,
        *,
        rate_limited_failure: bool,
        stop_event: threading.Event | None = None,
    ) -> bool:
        """Update consecutive RL failure counter; returns True if circuit breaker tripped."""
        tripped = False
        with self._lock:
            if rate_limited_failure:
                self.consecutive_rl_failures += 1
                if self.consecutive_rl_failures >= self.max_consecutive_failures:
                    self.stopped_rate_limit = True
                    tripped = True
            else:
                self.consecutive_rl_failures = 0
        if tripped:
            if stop_event:
                stop_event.set()
            if self._logger:
                self._logger.info(
                    f"STOPPED_RATE_LIMIT consecutive_failures={self.consecutive_rl_failures} "
                    f"(API keys appear exhausted; remaining pending URLs preserved for resume)"
                )
        return tripped


def _is_transient_submit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "http timeout" in msg or "connection error" in msg or "timed out" in msg


def _submit_with_backpressure(
    client: Any,
    submit_fn: Callable[[], dict[str, Any]],
    *,
    submit_lock: threading.Lock | None,
    max_active: int | None,
    backpressure_interval: float,
    rate_limit_gate: SharedRateLimitGate | None,
    source_tag: str,
    stop_event: threading.Event | None,
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
    on_backpressure: Callable[[str], None] | None,
    on_before_post: Callable[[], None] | None = None,
) -> dict[str, Any] | None:
    """Wait until backend active count < max_active, then invoke submit_fn while holding submit_lock.

    Returns None if interrupted by stop_event.
    """
    check_busy = max_active is not None and max_active > 0 and hasattr(client, "get_busy_count")
    last_logged_active: int | None = None
    last_log_at = 0.0
    busy_errors = 0

    while True:
        if stop_event and stop_event.is_set():
            return None
        if rate_limit_gate is not None:
            if not rate_limit_gate.wait_if_cooling(stop_event=stop_event, source_tag=source_tag):
                return None

        busy_active: int | None = None
        busy_exc: Exception | None = None

        if submit_lock is not None:
            submit_lock.acquire()
        try:
            if check_busy:
                try:
                    active = int(client.get_busy_count())
                    busy_errors = 0
                except AuthError:
                    raise
                except Exception as exc:
                    busy_exc = exc
                else:
                    if active >= int(max_active):
                        busy_active = active
                    else:
                        if on_before_post:
                            on_before_post()
                        return submit_fn()
            else:
                if on_before_post:
                    on_before_post()
                return submit_fn()
        finally:
            if submit_lock is not None:
                submit_lock.release()

        if busy_exc is not None:
            busy_errors += 1
            if busy_errors >= 6:
                raise busy_exc
            if on_backpressure:
                on_backpressure(
                    f"busy check error ({_short_text(str(busy_exc), 100)}); "
                    f"retrying in {backpressure_interval:.0f}s ({busy_errors}/6)"
                )
        elif busy_active is not None:
            now = monotonic_fn()
            if (
                last_logged_active != busy_active
                or (now - last_log_at) >= 30.0
            ):
                if on_backpressure:
                    on_backpressure(f"active={busy_active} limit={int(max_active)}")
                last_logged_active = busy_active
                last_log_at = now

        if not _interruptible_sleep(
            backpressure_interval, stop_event=stop_event, sleep_fn=sleep_fn
        ):
            return None


def _reconcile_material_after_timeout(
    client: Any,
    url: str,
    *,
    on_retry: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """After a POST timeout, inspect recent backend materials to see if the card was already created."""
    if not hasattr(client, "find_material_by_url"):
        return None
    try:
        existing = client.find_material_by_url(url)
    except AuthError:
        raise
    except Exception:
        return None
    if not existing or not existing.get("id"):
        return None
    existing_status = str(
        existing.get("processing_status") or existing.get("status") or "queued"
    ).lower()
    if on_retry:
        on_retry(
            f"Found already-created material after POST timeout: "
            f"id={existing['id']} status={existing_status}"
        )
    return {
        "id": existing["id"],
        "status": existing_status,
        "already_exists": False,
        "_recovered_after_timeout": True,
        "_existing_title": existing.get("title"),
    }


def process_single_url(
    client: Any,
    url: str,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    poll_timeout: float = 300.0,
    rate_limit_wait: float = DEFAULT_RATE_LIMIT_WAIT,
    max_rate_limit_retries: int = DEFAULT_RATE_LIMIT_RETRIES,
    heartbeat_sec: float = DEFAULT_HEARTBEAT_SEC,
    rate_limit_gate: SharedRateLimitGate | None = None,
    submit_lock: threading.Lock | None = None,
    max_active: int | None = None,
    backpressure_interval: float = DEFAULT_BACKPRESSURE_INTERVAL,
    source_tag: str = "",
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    stop_event: threading.Event | None = None,
    on_retry: Callable[[str], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_backpressure: Callable[[str], None] | None = None,
    on_submit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Submit one URL (guarded by backend backpressure), poll until terminal status, handle 429 + /reprocess."""
    max_attempts = max(1, int(max_rate_limit_retries) + 1)
    material_id: str | None = None
    transient_submit_retried = False
    submit_logged = False

    for attempt in range(max_attempts):
        if stop_event and stop_event.is_set():
            return {
                "status": "cancelled",
                "material_id": material_id,
                "title": None,
                "error": "Interrupted",
                "hit_remote": False,
                "rate_limited": False,
            }

        if rate_limit_gate is not None:
            if not rate_limit_gate.wait_if_cooling(stop_event=stop_event, source_tag=source_tag):
                return {
                    "status": "cancelled",
                    "material_id": material_id,
                    "title": None,
                    "error": "Interrupted",
                    "hit_remote": False,
                    "rate_limited": False,
                }

        # Submit new URL or reprocess existing failed material (strictly guarded by backpressure + submit_lock)
        enq: dict[str, Any]
        while True:
            is_reprocess = bool(
                material_id and attempt > 0 and hasattr(client, "reprocess_material")
            )
            submit_fn: Callable[[], dict[str, Any]] = (
                (lambda mid=material_id: client.reprocess_material(mid, only="full"))  # type: ignore[arg-type]
                if is_reprocess
                else (lambda u=url: client.enqueue_url(u))
            )
            def _mark_submit() -> None:
                nonlocal submit_logged
                if not submit_logged and on_submit:
                    submit_logged = True
                    on_submit()

            try:
                submitted = _submit_with_backpressure(
                    client,
                    submit_fn,
                    submit_lock=submit_lock,
                    max_active=max_active,
                    backpressure_interval=backpressure_interval,
                    rate_limit_gate=rate_limit_gate,
                    source_tag=source_tag,
                    stop_event=stop_event,
                    sleep_fn=sleep_fn,
                    monotonic_fn=monotonic_fn,
                    on_backpressure=on_backpressure,
                    on_before_post=_mark_submit,
                )
                if submitted is None:
                    return {
                        "status": "cancelled",
                        "material_id": material_id,
                        "title": None,
                        "error": "Interrupted",
                        "hit_remote": False,
                        "rate_limited": False,
                    }
                enq = submitted
                break
            except AuthError:
                raise
            except Exception as exc:
                if is_rate_limit_error(exc):
                    if attempt < max_attempts - 1:
                        wait_s = parse_rate_limit_wait(
                            exc,
                            retry_index=attempt,
                            base_wait=rate_limit_wait,
                        )
                        if rate_limit_gate is not None:
                            rate_limit_gate.trigger_cooldown(
                                wait_s, reason=str(exc), source_tag=source_tag
                            )
                            if not rate_limit_gate.wait_if_cooling(
                                stop_event=stop_event, source_tag=source_tag
                            ):
                                return {
                                    "status": "cancelled",
                                    "material_id": material_id,
                                    "title": None,
                                    "error": "Interrupted",
                                    "hit_remote": False,
                                    "rate_limited": False,
                                }
                        else:
                            if on_retry:
                                on_retry(
                                    f"Rate limit on submit ({exc}); waiting {wait_s:.0f}s "
                                    f"(retry {attempt + 1}/{max_attempts - 1})"
                                )
                            if not _interruptible_sleep(
                                wait_s, stop_event=stop_event, sleep_fn=sleep_fn
                            ):
                                return {
                                    "status": "cancelled",
                                    "material_id": material_id,
                                    "title": None,
                                    "error": "Interrupted",
                                    "hit_remote": False,
                                    "rate_limited": False,
                                }
                        enq = {}
                        break
                    return {
                        "status": "failed",
                        "material_id": material_id,
                        "title": None,
                        "error": str(exc),
                        "hit_remote": True,
                        "rate_limited": True,
                    }

                if _is_transient_submit_error(exc):
                    if not is_reprocess:
                        recovered = _reconcile_material_after_timeout(
                            client, url, on_retry=on_retry
                        )
                        if recovered is not None:
                            enq = recovered
                            break

                    if not transient_submit_retried:
                        transient_submit_retried = True
                        if on_retry:
                            on_retry(f"Transient submit error ({exc}); retrying once in 5s")
                        if not _interruptible_sleep(5.0, stop_event=stop_event, sleep_fn=sleep_fn):
                            return {
                                "status": "cancelled",
                                "material_id": material_id,
                                "title": None,
                                "error": "Interrupted",
                                "hit_remote": False,
                                "rate_limited": False,
                            }
                        continue

                return {
                    "status": "failed",
                    "material_id": material_id,
                    "title": None,
                    "error": str(exc),
                    "hit_remote": True,
                    "rate_limited": False,
                }

        if not enq:
            # Handled rate limit above -> move to next attempt
            continue

        material_id = str(enq.get("id") or material_id or "")
        already_exists = bool(enq.get("already_exists", False))
        recovered_after_timeout = bool(enq.get("_recovered_after_timeout", False))
        enq_status = str(enq.get("status") or "").lower()

        if recovered_after_timeout and enq_status == "done":
            return {
                "status": "done",
                "material_id": material_id or None,
                "title": enq.get("_existing_title"),
                "error": None,
                "hit_remote": True,
                "rate_limited": False,
            }

        if attempt == 0 and already_exists and enq_status == "done":
            title: str | None = None
            if material_id:
                try:
                    detail = client.get_material(material_id)
                    title = detail.get("title")
                except Exception:
                    pass
            return {
                "status": "duplicate",
                "material_id": material_id or None,
                "title": title,
                "error": None,
                "hit_remote": False,
                "rate_limited": False,
            }

        if not material_id:
            return {
                "status": "failed",
                "material_id": None,
                "title": None,
                "error": f"Unexpected enqueue response without id: {enq}",
                "hit_remote": True,
                "rate_limited": False,
            }

        started_at = monotonic_fn()
        deadline = started_at + poll_timeout
        last_status = enq_status or "queued"
        last_stage: str | None = None
        last_heartbeat = started_at
        rate_limited_in_poll = False
        poll_rl_error: Any = None
        poll_error: str | None = None

        while True:
            if stop_event and stop_event.is_set():
                return {
                    "status": "cancelled",
                    "material_id": material_id,
                    "title": None,
                    "error": "Interrupted",
                    "hit_remote": False,
                    "rate_limited": False,
                }

            remaining_before = deadline - monotonic_fn()
            try:
                if isinstance(client, ChronicumClient):
                    req_timeout = min(client.timeout, max(3.0, remaining_before))
                    detail = client.get_material(material_id, timeout=req_timeout)
                else:
                    detail = client.get_material(material_id)

                proc_status = str(
                    detail.get("processing_status") or detail.get("status") or ""
                ).lower()
                proc_stage = detail.get("processing_stage")
                last_status = proc_status or last_status

                if proc_status == "done":
                    return {
                        "status": "done",
                        "material_id": material_id,
                        "title": detail.get("title"),
                        "error": None,
                        "hit_remote": True,
                        "rate_limited": False,
                    }
                if proc_status == "failed":
                    err_msg = str(detail.get("error_message") or "Processing failed")
                    if is_rate_limit_error(err_msg):
                        if attempt < max_attempts - 1:
                            rate_limited_in_poll = True
                            poll_rl_error = err_msg
                            break
                        return {
                            "status": "failed",
                            "material_id": material_id,
                            "title": detail.get("title"),
                            "error": err_msg,
                            "hit_remote": True,
                            "rate_limited": True,
                        }
                    return {
                        "status": "failed",
                        "material_id": material_id,
                        "title": detail.get("title"),
                        "error": err_msg,
                        "hit_remote": True,
                        "rate_limited": False,
                    }

                now = monotonic_fn()
                stage_changed = bool(proc_stage and proc_stage != last_stage)
                heartbeat_due = heartbeat_sec > 0 and (now - last_heartbeat) >= heartbeat_sec
                if proc_stage:
                    last_stage = str(proc_stage)
                if on_progress and (stage_changed or heartbeat_due):
                    elapsed = int(max(0.0, now - started_at))
                    on_progress(
                        f"id={material_id} status={last_status} stage={last_stage or '-'} elapsed={elapsed}s"
                    )
                    last_heartbeat = now

            except AuthError:
                raise
            except HttpApiError as exc:
                if exc.status_code == 429:
                    wait_s = parse_rate_limit_wait(
                        exc,
                        retry_index=attempt,
                        base_wait=rate_limit_wait,
                    )
                    if rate_limit_gate is not None:
                        rate_limit_gate.trigger_cooldown(
                            wait_s, reason=str(exc), source_tag=source_tag
                        )
                    if (monotonic_fn() + wait_s) < deadline:
                        if rate_limit_gate is not None:
                            ok = rate_limit_gate.wait_if_cooling(
                                stop_event=stop_event, source_tag=source_tag
                            )
                        else:
                            if on_retry:
                                on_retry(f"Poll 429 on id={material_id}; waiting {wait_s:.0f}s")
                            ok = _interruptible_sleep(
                                wait_s, stop_event=stop_event, sleep_fn=sleep_fn
                            )
                        if not ok:
                            return {
                                "status": "cancelled",
                                "material_id": material_id,
                                "title": None,
                                "error": "Interrupted",
                                "hit_remote": False,
                                "rate_limited": False,
                            }
                        continue
                poll_error = str(exc)
            except Exception as exc:
                poll_error = str(exc)

            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                stage_info = f", last_stage={last_stage}" if last_stage else ""
                extra = f"; last_error={poll_error}" if poll_error else ""
                return {
                    "status": "failed",
                    "material_id": material_id,
                    "title": None,
                    "error": f"Timeout after {poll_timeout:.0f}s (last_status={last_status}{stage_info}{extra})",
                    "hit_remote": True,
                    "rate_limited": False,
                }
            if not _interruptible_sleep(
                min(poll_interval, remaining), stop_event=stop_event, sleep_fn=sleep_fn
            ):
                return {
                    "status": "cancelled",
                    "material_id": material_id,
                    "title": None,
                    "error": "Interrupted",
                    "hit_remote": False,
                    "rate_limited": False,
                }

        if rate_limited_in_poll and attempt < max_attempts - 1:
            wait_s = parse_rate_limit_wait(
                poll_rl_error,
                retry_index=attempt,
                base_wait=rate_limit_wait,
            )
            if on_retry:
                on_retry(
                    f"Material id={material_id} hit rate limit ({_short_text(str(poll_rl_error), 120)}); "
                    f"reprocess retry {attempt + 1}/{max_attempts - 1} after {wait_s:.0f}s"
                )
            if rate_limit_gate is not None:
                rate_limit_gate.trigger_cooldown(
                    wait_s, reason=str(poll_rl_error), source_tag=source_tag
                )
                if not rate_limit_gate.wait_if_cooling(
                    stop_event=stop_event, source_tag=source_tag
                ):
                    return {
                        "status": "cancelled",
                        "material_id": material_id,
                        "title": None,
                        "error": "Interrupted",
                        "hit_remote": False,
                        "rate_limited": False,
                    }
            else:
                if not _interruptible_sleep(wait_s, stop_event=stop_event, sleep_fn=sleep_fn):
                    return {
                        "status": "cancelled",
                        "material_id": material_id,
                        "title": None,
                        "error": "Interrupted",
                        "hit_remote": False,
                        "rate_limited": False,
                    }
            continue

    return {
        "status": "failed",
        "material_id": material_id,
        "title": None,
        "error": "Exhausted rate-limit retries",
        "hit_remote": True,
        "rate_limited": True,
    }


def _short_text(value: str | None, max_len: int = 90) -> str:
    clean = " ".join((value or "").split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


@dataclass
class RunSummary:
    processed: int
    done: int
    duplicate: int
    failed: int
    auth_error: str | None = None
    stopped_rate_limit: bool = False
    interrupted: bool = False


def run_queue(
    urls: list[str],
    state: dict[str, Any],
    state_file: Path,
    client: Any,
    logger: QueueLogger,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_active: int | None = None,
    backpressure_interval: float = DEFAULT_BACKPRESSURE_INTERVAL,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    poll_timeout: float = 300.0,
    rate_limit_wait: float = DEFAULT_RATE_LIMIT_WAIT,
    max_rate_limit_retries: int = DEFAULT_RATE_LIMIT_RETRIES,
    max_consecutive_rate_limits: int = DEFAULT_MAX_CONSECUTIVE_RATE_LIMITS,
    heartbeat_sec: float = DEFAULT_HEARTBEAT_SEC,
    delay_min: float | None = None,
    delay_max: float | None = None,
    stagger_min: float = DEFAULT_STAGGER_RANGE[0],
    stagger_max: float = DEFAULT_STAGGER_RANGE[1],
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    rng: Callable[[float, float], float] = random.uniform,
) -> RunSummary:
    """Process `urls` via `concurrency` continuous slots with backend-aware backpressure."""
    total = len(urls)
    workers = max(1, min(int(concurrency), max(1, total)))
    effective_max_active = max(1, int(max_active if max_active is not None else concurrency))
    work_q: queue.Queue[tuple[int, str]] = queue.Queue()
    for idx, url in enumerate(urls, 1):
        work_q.put((idx, url))

    state_lock = threading.Lock()
    submit_lock = threading.Lock()
    stop_event = threading.Event()
    counters = {"done": 0, "duplicate": 0, "failed": 0}
    auth_err_box: list[str] = []
    rl_gate = SharedRateLimitGate(
        max_consecutive_failures=max_consecutive_rate_limits,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
        logger=logger,
    )

    def slot_worker(slot_num: int) -> None:
        slot_tag = f"[W{slot_num}]" if workers > 1 else ""
        slot_prefix = f"{slot_tag} " if slot_tag else ""

        # One-time startup stagger so slot 2 doesn't hit Instagram in the exact same ms as slot 1
        if slot_num > 1 and stagger_max > 0:
            lo = max(0.0, min(stagger_min, stagger_max))
            hi = max(lo, stagger_max)
            stagger = rng(lo, hi) * (slot_num - 1)
            if stagger > 0:
                logger.info(f"{slot_prefix}STAGGER {stagger:.1f}s")
                if not _interruptible_sleep(stagger, stop_event=stop_event, sleep_fn=sleep_fn):
                    return

        while not stop_event.is_set():
            if not rl_gate.wait_if_cooling(stop_event=stop_event, source_tag=slot_tag):
                return
            if stop_event.is_set():
                return

            try:
                idx, url = work_q.get_nowait()
            except queue.Empty:
                return

            progress = f"{slot_prefix}[{idx}/{total}]"

            try:
                result = process_single_url(
                    client,
                    url,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    rate_limit_wait=rate_limit_wait,
                    max_rate_limit_retries=max_rate_limit_retries,
                    heartbeat_sec=heartbeat_sec,
                    rate_limit_gate=rl_gate,
                    submit_lock=submit_lock,
                    max_active=effective_max_active,
                    backpressure_interval=backpressure_interval,
                    source_tag=progress,
                    sleep_fn=sleep_fn,
                    monotonic_fn=monotonic_fn,
                    stop_event=stop_event,
                    on_retry=lambda msg, p=progress: logger.info(f"{p} RETRY {msg}"),
                    on_progress=lambda msg, p=progress: logger.info(f"{p} WAIT {msg}"),
                    on_backpressure=lambda msg, p=progress: logger.info(f"{p} BACKPRESSURE {msg}"),
                    on_submit=lambda p=progress, u=url: logger.info(f"{p} SUBMIT {u}"),
                )
            except AuthError as exc:
                with state_lock:
                    if not auth_err_box:
                        auth_err_box.append(str(exc))
                stop_event.set()
                logger.info(f"{progress} ABORT auth error: {exc}")
                return

            st = result["status"]
            if st == "cancelled":
                return

            mid = result.get("material_id")
            title = result.get("title")
            error = result.get("error")
            rate_limited = bool(result.get("rate_limited", False))

            with state_lock:
                set_item_state(
                    state,
                    url,
                    status=st,
                    material_id=mid,
                    title=title,
                    error=error,
                )
                save_state(state_file, state)
                if st == "done":
                    counters["done"] += 1
                elif st == "duplicate":
                    counters["duplicate"] += 1
                else:
                    counters["failed"] += 1

            if st == "done":
                logger.info(
                    f"{progress} DONE id={mid or '-'} title={_short_text(title) or '-'}"
                )
            elif st == "duplicate":
                logger.info(
                    f"{progress} DUPLICATE id={mid or '-'} title={_short_text(title) or '-'}"
                )
            else:
                rl_flag = " [rate_limit]" if rate_limited else ""
                logger.info(
                    f"{progress} FAILED{rl_flag} id={mid or '-'} error={_short_text(error, 160) or '-'}"
                )

            if rl_gate.record_item_outcome(
                rate_limited_failure=(st == "failed" and rate_limited),
                stop_event=stop_event,
            ):
                return

            if not work_q.empty() and not stop_event.is_set() and result.get("hit_remote", True):
                delay = choose_delay(
                    url,
                    delay_min=delay_min,
                    delay_max=delay_max,
                    rng=rng,
                )
                if delay > 0:
                    logger.info(f"{progress} SLEEP {delay:.1f}s")
                    if not _interruptible_sleep(delay, stop_event=stop_event, sleep_fn=sleep_fn):
                        return

    interrupted = False
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ingest-slot") as pool:
        futures = [pool.submit(slot_worker, slot_id) for slot_id in range(1, workers + 1)]
        try:
            for fut in futures:
                fut.result()
        except KeyboardInterrupt:
            interrupted = True
            stop_event.set()
            for fut in futures:
                try:
                    fut.result(timeout=5.0)
                except Exception:
                    pass

    processed = counters["done"] + counters["duplicate"] + counters["failed"]
    return RunSummary(
        processed=processed,
        done=counters["done"],
        duplicate=counters["duplicate"],
        failed=counters["failed"],
        auth_error=auth_err_box[0] if auth_err_box else None,
        stopped_rate_limit=rl_gate.stopped_rate_limit,
        interrupted=interrupted,
    )


def print_status_summary(
    queue_file: Path,
    state_file: Path,
    plan: QueuePlan,
    *,
    retry_failed: bool = False,
) -> None:
    print(f"Queue file : {queue_file} ({'exists' if queue_file.exists() else 'missing'})")
    print(f"State file : {state_file}")
    print(
        f"Summary    : total={plan.total_urls} | done={plan.done_count} | "
        f"duplicate={plan.duplicate_count} | failed={plan.failed_count} | pending={plan.pending_count}"
    )
    mode = "retry-failed" if retry_failed else "pending"
    print(f"Next run   : {len(plan.to_process)} item(s) ({mode})")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Continuous multi-slot batch URL ingestor for local Chronicum."
    )
    p.add_argument("--file", type=Path, default=DEFAULT_QUEUE_FILE, help="Input queue file (default: data/ingest_queue.txt)")
    p.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE, help="State JSON file (default: data/ingest_queue_state.json)")
    p.add_argument("--log", type=Path, default=DEFAULT_LOG_FILE, help="Log file (default: data/ingest_queue.log)")
    p.add_argument("--lock", type=Path, default=DEFAULT_LOCK_FILE, help="Lock file (default: data/ingest_queue.lock)")
    p.add_argument("--api-url", default=None, help=f"Chronicum API base URL (default: {DEFAULT_API_URL})")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help=f"Parallel worker slots (default: {DEFAULT_CONCURRENCY})")
    p.add_argument("--stagger-min", type=float, default=DEFAULT_STAGGER_RANGE[0], help="Min stagger delay in seconds between worker slot starts (default: 2)")
    p.add_argument("--stagger-max", type=float, default=DEFAULT_STAGGER_RANGE[1], help="Max stagger delay in seconds between worker slot starts (default: 4)")
    p.add_argument("--http-timeout", type=float, default=DEFAULT_HTTP_TIMEOUT, help=f"Hard socket/read timeout per HTTP request in seconds (default: {int(DEFAULT_HTTP_TIMEOUT)})")
    p.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help=f"Status poll interval in seconds (default: {int(DEFAULT_POLL_INTERVAL)})")
    p.add_argument("--backpressure-interval", type=float, default=DEFAULT_BACKPRESSURE_INTERVAL, help=f"Backpressure re-check interval when backend active >= concurrency (default: {int(DEFAULT_BACKPRESSURE_INTERVAL)})")
    p.add_argument("--poll-timeout", type=float, default=300.0, help="Per-item poll timeout in seconds (default: 300)")
    p.add_argument("--heartbeat", type=float, default=DEFAULT_HEARTBEAT_SEC, help="Poll heartbeat interval in seconds (default: 45)")
    p.add_argument("--delay-min", type=float, default=None, help="Override min pause between items in a slot")
    p.add_argument("--delay-max", type=float, default=None, help="Override max pause between items in a slot")
    p.add_argument("--rate-limit-wait", type=float, default=DEFAULT_RATE_LIMIT_WAIT, help="Base backoff seconds on 429/rate-limit (escalates 60->120s, default: 60)")
    p.add_argument("--rate-limit-retries", type=int, default=DEFAULT_RATE_LIMIT_RETRIES, help="Max retries per URL on 429/rate-limit (default: 2)")
    p.add_argument("--max-rate-limit-stops", type=int, default=DEFAULT_MAX_CONSECUTIVE_RATE_LIMITS, help="Stop queue after N consecutive rate-limit failures across URLs (default: 3)")
    p.add_argument("--dry-run", action="store_true", help="Preview parsed queue and actions without sending requests")
    p.add_argument("--status", action="store_true", help="Print short done/duplicate/failed/pending summary and exit")
    p.add_argument("--retry-failed", action="store_true", help="Retry only URLs marked failed in state")
    p.add_argument("--limit", type=int, default=None, help="Process at most N URLs in this run")
    p.add_argument("--quiet", action="store_true", help="Do not print log lines to stdout (still writes log file)")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = build_arg_parser().parse_args(argv)
    queue_file: Path = args.file
    state_file: Path = args.state
    log_file: Path = args.log
    lock_file: Path = args.lock
    concurrency = max(1, int(args.concurrency))

    urls = load_queue_urls(queue_file)
    state = load_state(state_file)
    plan = build_queue_plan(
        urls,
        state,
        retry_failed=args.retry_failed,
        limit=args.limit,
    )

    if args.status:
        print_status_summary(queue_file, state_file, plan, retry_failed=args.retry_failed)
        return 0

    if args.dry_run:
        api_url = resolve_api_url(args.api_url)
        has_token = bool(resolve_api_token())
        print_status_summary(queue_file, state_file, plan, retry_failed=args.retry_failed)
        print(
            f"API URL    : {api_url} | token={'configured' if has_token else 'MISSING'} | concurrency={concurrency}"
        )
        if not plan.to_process:
            print("Dry-run    : nothing to process.")
            return 0
        print("Dry-run    : queued URLs:")
        for idx, url in enumerate(plan.to_process, 1):
            kind = "instagram" if is_instagram_url(url) else "other"
            print(f"  [{idx}/{len(plan.to_process)}] ({kind}) {url}")
        return 0

    if not queue_file.exists():
        print(f"Queue file not found: {queue_file}", file=sys.stderr)
        return 1

    if not plan.to_process:
        print_status_summary(queue_file, state_file, plan, retry_failed=args.retry_failed)
        print("Nothing to process.")
        return 0

    token = resolve_api_token()
    if not token:
        print(
            "API token not found. Set CHRONICUM_API_TOKEN or configure 'chronicum' in ~/.cursor/mcp.json.",
            file=sys.stderr,
        )
        return 1

    api_url = resolve_api_url(args.api_url)
    logger = QueueLogger(log_file, quiet=args.quiet)
    client = ChronicumClient(api_url=api_url, token=token, timeout=args.http_timeout)

    try:
        lock = QueueLock(lock_file)
        lock.acquire()
    except QueueAlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        logger.info(
            f"START total={len(plan.to_process)} concurrency={concurrency} "
            f"max_active={concurrency} poll_interval={args.poll_interval:g}s "
            f"mode={'retry-failed' if args.retry_failed else 'pending'} "
            f"api={api_url} file={queue_file}"
        )

        summary = run_queue(
            plan.to_process,
            state,
            state_file,
            client,
            logger,
            concurrency=concurrency,
            max_active=concurrency,
            backpressure_interval=args.backpressure_interval,
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
            rate_limit_wait=args.rate_limit_wait,
            max_rate_limit_retries=args.rate_limit_retries,
            max_consecutive_rate_limits=args.max_rate_limit_stops,
            heartbeat_sec=args.heartbeat,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            stagger_min=args.stagger_min,
            stagger_max=args.stagger_max,
        )

        if summary.auth_error:
            return 1
        if summary.interrupted:
            logger.info(
                f"INTERRUPTED done={summary.done} duplicate={summary.duplicate} failed={summary.failed}"
            )
            return 130
        if summary.stopped_rate_limit:
            logger.info(
                f"SUMMARY stopped=rate_limit processed={summary.processed} "
                f"done={summary.done} duplicate={summary.duplicate} failed={summary.failed}"
            )
            return 3

        logger.info(
            f"SUMMARY processed={summary.processed} done={summary.done} "
            f"duplicate={summary.duplicate} failed={summary.failed}"
        )
        return 0
    except Exception as exc:
        logger.info(f"CRASH {type(exc).__name__}: {exc}")
        raise
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
