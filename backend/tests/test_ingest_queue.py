from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ingest_queue.py"

_spec = importlib.util.spec_from_file_location("ingest_queue", SCRIPT_PATH)
assert _spec and _spec.loader
iq = importlib.util.module_from_spec(_spec)
sys.modules["ingest_queue"] = iq
_spec.loader.exec_module(iq)


def test_extract_urls_parses_noisy_lines_and_deduplicates():
    raw = """
    # Сохранёнки из Instagram
       # ещё комментарий с https://example.com/ignored

    1) https://www.instagram.com/reel/DgPcKbqI2PB/?igsh=abc123 — классный рилс
    Повтор того же рилса: https://instagram.com/reel/DgPcKbqI2PB/
    YouTube: [смотреть](https://youtu.be/dQw4w9WgXcQ?si=xyz).
    Дубликат YouTube: https://www.youtube.com/watch?v=dQw4w9WgXcQ
    Статья (https://habr.com/ru/articles/12345/?utm_source=tg), и ещё https://en.wikipedia.org/wiki/Python_(programming_language).
    Не ссылка: http://localhost_broken и ftp://files.example.com/a.zip
    """
    urls = iq.extract_urls_from_text(raw)
    assert urls == [
        "https://www.instagram.com/reel/DgPcKbqI2PB/?igsh=abc123",
        "https://youtu.be/dQw4w9WgXcQ?si=xyz",
        "https://habr.com/ru/articles/12345/?utm_source=tg",
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
    ]


def test_state_resume_retry_failed_and_limit(tmp_path: Path):
    state_file = tmp_path / "ingest_queue_state.json"
    urls = [
        "https://www.instagram.com/reel/AAA111/",
        "https://www.instagram.com/reel/BBB222/",
        "https://www.instagram.com/reel/CCC333/",
        "https://www.instagram.com/reel/DDD444/",
        "https://example.com/article-1",
    ]

    state = iq.load_state(state_file)
    iq.set_item_state(state, urls[0], status="done", material_id="m1", title="Reel 1")
    iq.set_item_state(state, urls[1], status="duplicate", material_id="m2", title="Reel 2")
    iq.set_item_state(state, urls[2], status="failed", error="HTTP 500")
    iq.save_state(state_file, state)

    reloaded = iq.load_state(state_file)

    # Normal run skips done/duplicate/failed and processes pending URLs
    plan = iq.build_queue_plan(urls, reloaded)
    assert plan.done_count == 1
    assert plan.duplicate_count == 1
    assert plan.failed_count == 1
    assert plan.pending_count == 2
    assert plan.to_process == [urls[3], urls[4]]

    # Same reel pasted later with tracking query still resolves to done via canonical key
    with_tracking = ["https://instagram.com/reel/AAA111/?igsh=newtoken", urls[3]]
    plan_canonical = iq.build_queue_plan(with_tracking, reloaded)
    assert plan_canonical.done_count == 1
    assert plan_canonical.to_process == [urls[3]]

    # --limit restricts batch size
    plan_limited = iq.build_queue_plan(urls, reloaded, limit=1)
    assert plan_limited.to_process == [urls[3]]

    # --retry-failed selects only failed items
    plan_retry = iq.build_queue_plan(urls, reloaded, retry_failed=True)
    assert plan_retry.to_process == [urls[2]]


def test_resolve_api_token_env_and_mcp_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mcp_file = tmp_path / "mcp.json"
    mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "chronicum": {
                        "type": "http",
                        "url": "http://127.0.0.1:8010/mcp/",
                        "headers": {"Authorization": "Bearer mcp-secret-value"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("CHRONICUM_API_TOKEN", raising=False)
    assert iq.resolve_api_token(mcp_file) == "mcp-secret-value"

    monkeypatch.setenv("CHRONICUM_API_TOKEN", "env-override-value")
    assert iq.resolve_api_token(mcp_file) == "env-override-value"


def test_process_single_url_duplicate_done_and_429_retry():
    class FakeClient:
        def __init__(self, enqueue_seq: list[Any], poll_seq: list[Any]) -> None:
            self._enqueue_seq = list(enqueue_seq)
            self._poll_seq = list(poll_seq)
            self.enqueue_calls = 0
            self.poll_calls = 0

        def enqueue_url(self, url: str) -> dict[str, Any]:
            self.enqueue_calls += 1
            nxt = self._enqueue_seq.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        def get_material(self, material_id: str) -> dict[str, Any]:
            self.poll_calls += 1
            return self._poll_seq.pop(0)

    # 1. Existing finished material -> duplicate without polling loop
    c_dup = FakeClient(
        enqueue_seq=[{"id": "m-dup", "status": "done", "message": "Уже в архиве", "already_exists": True}],
        poll_seq=[{"id": "m-dup", "title": "Existing Reel", "processing_status": "done"}],
    )
    res_dup = iq.process_single_url(c_dup, "https://www.instagram.com/reel/DUP/", sleep_fn=lambda _: None)
    assert res_dup["status"] == "duplicate"
    assert res_dup["material_id"] == "m-dup"
    assert res_dup["hit_remote"] is False

    # 2. HTTP 429 on submit -> backs off and retries once -> polls queued -> done
    sleeps: list[float] = []
    c_retry = FakeClient(
        enqueue_seq=[
            iq.HttpApiError(429, "Too Many Requests"),
            {"id": "m-ok", "status": "queued", "message": "instagram accepted", "already_exists": False},
        ],
        poll_seq=[
            {"id": "m-ok", "processing_status": "processing", "title": None},
            {"id": "m-ok", "processing_status": "done", "title": "Parsed Reel"},
        ],
    )
    res_ok = iq.process_single_url(
        c_retry,
        "https://www.instagram.com/reel/OK/",
        poll_interval=0.1,
        rate_limit_wait=60.0,
        sleep_fn=sleeps.append,
    )
    assert res_ok["status"] == "done"
    assert res_ok["material_id"] == "m-ok"
    assert res_ok["title"] == "Parsed Reel"
    assert c_retry.enqueue_calls == 2
    assert c_retry.poll_calls == 2
    assert 60.0 in sleeps


def test_queue_lock_blocks_double_run_and_clears_stale(tmp_path: Path):
    lock_file = tmp_path / "ingest_queue.lock"
    with iq.QueueLock(lock_file):
        assert lock_file.exists()
        with pytest.raises(iq.QueueAlreadyRunningError):
            iq.QueueLock(lock_file).acquire()
    assert not lock_file.exists()

    # Stale lock with dead PID is automatically replaced
    lock_file.write_text(json.dumps({"pid": 9999999}), encoding="utf-8")
    with iq.QueueLock(lock_file):
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        assert data["pid"] != 9999999
    assert not lock_file.exists()


def test_run_queue_concurrency_two_overlaps_and_isolates_timeout(tmp_path: Path):
    state_file = tmp_path / "state.json"
    log_file = tmp_path / "queue.log"
    urls = [
        "https://www.instagram.com/p/SLOT_A1/",
        "https://www.instagram.com/p/SLOT_B1/",
        "https://www.instagram.com/p/SLOT_A2/",
        "https://www.instagram.com/p/SLOT_STUCK/",
    ]

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    poll_counts: dict[str, int] = {}

    class ParallelFakeClient:
        def enqueue_url(self, url: str) -> dict[str, Any]:
            nonlocal in_flight, max_in_flight
            with lock:
                in_flight += 1
                if in_flight > max_in_flight:
                    max_in_flight = in_flight
            mid = url.rstrip("/").split("/")[-1]
            # Hold briefly so slot 2 enters concurrently
            time.sleep(0.05)
            return {
                "id": f"id-{mid}",
                "status": "queued",
                "message": "instagram accepted",
                "already_exists": False,
            }

        def get_material(self, material_id: str) -> dict[str, Any]:
            nonlocal in_flight
            if material_id == "id-SLOT_STUCK":
                # Never transitions out of processing -> must hit poll_timeout cleanly
                return {
                    "id": material_id,
                    "processing_status": "processing",
                    "processing_stage": "fetch",
                    "title": None,
                }
            with lock:
                n = poll_counts.get(material_id, 0) + 1
                poll_counts[material_id] = n
            if n == 1:
                time.sleep(0.04)
                return {
                    "id": material_id,
                    "processing_status": "processing",
                    "processing_stage": "transcribe",
                    "title": None,
                }
            with lock:
                in_flight = max(0, in_flight - 1)
            return {
                "id": material_id,
                "processing_status": "done",
                "processing_stage": "full",
                "title": f"Title {material_id}",
            }

    state = iq.load_state(state_file)
    logger = iq.QueueLogger(log_file, quiet=True)
    summary = iq.run_queue(
        urls,
        state,
        state_file,
        ParallelFakeClient(),
        logger,
        concurrency=2,
        poll_interval=0.02,
        poll_timeout=0.12,
        heartbeat_sec=0.05,
        delay_min=0.0,
        delay_max=0.0,
        stagger_min=0.01,
        stagger_max=0.01,
    )

    assert max_in_flight == 2
    assert summary.processed == 4
    assert summary.done == 3
    assert summary.failed == 1
    assert summary.duplicate == 0

    reloaded = iq.load_state(state_file)
    assert iq.get_item_state(reloaded, urls[0])["status"] == "done"
    assert iq.get_item_state(reloaded, urls[1])["status"] == "done"
    assert iq.get_item_state(reloaded, urls[2])["status"] == "done"
    stuck_entry = iq.get_item_state(reloaded, urls[3])
    assert stuck_entry["status"] == "failed"
    assert "Timeout" in (stuck_entry["error"] or "")

    log_text = log_file.read_text(encoding="utf-8")
    assert "[W1]" in log_text and "[W2]" in log_text
    assert "WAIT" in log_text


def test_rate_limit_detection_wait_parsing_reprocess_and_shared_cooldown(tmp_path: Path):
    # 1. Detection across Groq/Gemini/HTTP/RU rate-limit error messages
    for msg in (
        "Error code: 429 - rate_limit_exceeded on tokens per minute (TPM): Please try again in 12.5s",
        "RESOURCE_EXHAUSTED: Gemini quota exceeded for RPM",
        "Requests per day (RPD) limit reached",
        "Превышен лимит запросов, повторите через 30 сек",
        "Too Many Requests, retry-after: 45",
    ):
        assert iq.is_rate_limit_error(msg), f"Expected rate-limit match for: {msg}"
    assert not iq.is_rate_limit_error("Instagram post is private or deleted (404)")

    # 2. Wait parsing + 60s -> 120s escalation
    assert iq.parse_rate_limit_wait("Please try again in 1m10s", retry_index=0) == pytest.approx(72.0)
    assert iq.parse_rate_limit_wait("Rate limit exceeded", retry_index=0, base_wait=60.0) == 60.0
    assert iq.parse_rate_limit_wait("Rate limit exceeded", retry_index=1, base_wait=60.0) == 120.0

    # 3. End-to-end: material fails with Groq 429 error_message -> shared cooldown -> /reprocess succeeds
    state_file = tmp_path / "rl_state.json"
    log_file = tmp_path / "rl_queue.log"
    urls = [
        "https://www.instagram.com/p/RL_ONE/",
        "https://www.instagram.com/p/RL_TWO/",
    ]

    lock = threading.Lock()
    reprocess_calls: list[tuple[str, str]] = []
    cooldown_triggered_at: list[float] = []
    second_submit_at: list[float] = []
    attempts_by_mid: dict[str, int] = {}

    class RateLimitReprocessClient:
        def enqueue_url(self, url: str) -> dict[str, Any]:
            mid = url.rstrip("/").split("/")[-1]
            if mid == "RL_TWO":
                second_submit_at.append(time.monotonic())
            return {
                "id": f"mat-{mid}",
                "status": "queued",
                "message": "instagram accepted",
                "already_exists": False,
            }

        def reprocess_material(self, material_id: str, *, only: str = "full") -> dict[str, Any]:
            with lock:
                reprocess_calls.append((material_id, only))
            return {"id": material_id, "only": only, "status": "queued"}

        def get_material(self, material_id: str) -> dict[str, Any]:
            with lock:
                n = attempts_by_mid.get(material_id, 0) + 1
                attempts_by_mid[material_id] = n
            if material_id == "mat-RL_ONE" and n == 1:
                cooldown_triggered_at.append(time.monotonic())
                return {
                    "id": material_id,
                    "processing_status": "failed",
                    "error_message": "Groq 429 rate_limit_exceeded (TPM): try again in 150ms",
                }
            return {
                "id": material_id,
                "processing_status": "done",
                "title": f"OK {material_id}",
            }

    state = iq.load_state(state_file)
    logger = iq.QueueLogger(log_file, quiet=True)
    # Use a short base wait (0.15s) and verify slot 2 respects shared cooldown + slot 1 calls /reprocess
    summary = iq.run_queue(
        urls,
        state,
        state_file,
        RateLimitReprocessClient(),
        logger,
        concurrency=2,
        poll_interval=0.01,
        poll_timeout=5.0,
        rate_limit_wait=0.15,
        max_rate_limit_retries=2,
        delay_min=0.0,
        delay_max=0.0,
        stagger_min=0.04,
        stagger_max=0.04,
    )

    assert summary.done == 2
    assert summary.failed == 0
    assert reprocess_calls == [("mat-RL_ONE", "full")]
    log_text = log_file.read_text(encoding="utf-8")
    assert "RATE_LIMIT_COOLDOWN" in log_text
    assert "COOLDOWN_WAIT" in log_text
    assert cooldown_triggered_at and second_submit_at
    assert second_submit_at[0] > cooldown_triggered_at[0]


def test_continuous_two_slots_fast_slot_pulls_third_while_first_still_running(tmp_path: Path):
    state_file = tmp_path / "cont_state.json"
    log_file = tmp_path / "cont_queue.log"
    urls = [
        "https://www.instagram.com/p/SLOW_1/",
        "https://www.instagram.com/p/FAST_2/",
        "https://www.instagram.com/p/FAST_3/",
    ]

    slow_done_event = threading.Event()
    fast3_started_while_slow_active = False

    class ContinuousClient:
        def enqueue_url(self, url: str) -> dict[str, Any]:
            nonlocal fast3_started_while_slow_active
            mid = url.rstrip("/").split("/")[-1]
            if mid == "FAST_3" and not slow_done_event.is_set():
                fast3_started_while_slow_active = True
            return {"id": f"id-{mid}", "status": "queued", "already_exists": False}

        def get_material(self, material_id: str) -> dict[str, Any]:
            if material_id == "id-SLOW_1":
                if not slow_done_event.is_set():
                    return {"id": material_id, "processing_status": "processing", "processing_stage": "asr"}
                return {"id": material_id, "processing_status": "done", "title": "Slow 1"}
            if material_id == "id-FAST_3":
                # Release SLOW_1 only AFTER FAST_3 is already being polled!
                slow_done_event.set()
            return {"id": material_id, "processing_status": "done", "title": material_id}

    state = iq.load_state(state_file)
    logger = iq.QueueLogger(log_file, quiet=True)
    summary = iq.run_queue(
        urls,
        state,
        state_file,
        ContinuousClient(),
        logger,
        concurrency=2,
        poll_interval=0.01,
        poll_timeout=3.0,
        delay_min=0.0,
        delay_max=0.0,
        stagger_min=0.01,
        stagger_max=0.01,
    )

    assert fast3_started_while_slow_active is True
    assert summary.done == 3
    assert summary.failed == 0


def test_circuit_breaker_stops_after_three_consecutive_rate_limits_preserving_pending(tmp_path: Path):
    state_file = tmp_path / "cb_state.json"
    log_file = tmp_path / "cb_queue.log"
    urls = [f"https://www.instagram.com/p/EXHAUST_{i}/" for i in range(1, 6)]

    class AlwaysRateLimitedClient:
        def enqueue_url(self, url: str) -> dict[str, Any]:
            mid = url.rstrip("/").split("/")[-1]
            return {"id": f"id-{mid}", "status": "queued", "already_exists": False}

        def reprocess_material(self, material_id: str, *, only: str = "full") -> dict[str, Any]:
            return {"id": material_id, "only": only, "status": "queued"}

        def get_material(self, material_id: str) -> dict[str, Any]:
            return {
                "id": material_id,
                "processing_status": "failed",
                "error_message": "429 RESOURCE_EXHAUSTED: daily RPD quota exceeded",
            }

    state = iq.load_state(state_file)
    logger = iq.QueueLogger(log_file, quiet=True)
    summary = iq.run_queue(
        urls,
        state,
        state_file,
        AlwaysRateLimitedClient(),
        logger,
        concurrency=1,
        poll_interval=0.005,
        poll_timeout=2.0,
        rate_limit_wait=0.0,
        max_rate_limit_retries=2,
        max_consecutive_rate_limits=3,
        delay_min=0.0,
        delay_max=0.0,
        stagger_min=0.0,
        stagger_max=0.0,
    )

    assert summary.stopped_rate_limit is True
    assert summary.failed == 3
    assert summary.done == 0

    reloaded = iq.load_state(state_file)
    # Remaining URLs 4 & 5 must stay untouched (pending, not failed)
    assert iq.get_item_state(reloaded, urls[3]) is None
    assert iq.get_item_state(reloaded, urls[4]) is None
    plan = iq.build_queue_plan(urls, reloaded)
    assert plan.failed_count == 3
    assert plan.pending_count == 2
    assert "STOPPED_RATE_LIMIT" in log_file.read_text(encoding="utf-8")


def test_backpressure_waits_while_backend_busy_and_submits_exactly_one_when_slot_opens(tmp_path: Path):
    state_file = tmp_path / "bp_state.json"
    log_file = tmp_path / "bp_queue.log"
    urls = [
        "https://www.instagram.com/p/BP_1/",
        "https://www.instagram.com/p/BP_2/",
    ]

    lock = threading.Lock()
    external_active = 2
    active_ids: set[str] = set()
    submitted_urls: list[str] = []
    max_observed_active = 0
    release_first = threading.Event()
    first_submitted = threading.Event()
    busy_checked_twice = threading.Event()
    busy_checks = 0

    class BackpressureClient:
        def get_busy_count(self) -> int:
            nonlocal busy_checks
            with lock:
                busy_checks += 1
                if busy_checks >= 2:
                    busy_checked_twice.set()
                return external_active + len(active_ids)

        def enqueue_url(self, url: str) -> dict[str, Any]:
            nonlocal max_observed_active
            mid = url.rstrip("/").split("/")[-1]
            with lock:
                submitted_urls.append(url)
                active_ids.add(f"id-{mid}")
                total_now = external_active + len(active_ids)
                if total_now > max_observed_active:
                    max_observed_active = total_now
            first_submitted.set()
            # Small delay inside POST to prove submit_lock prevents a 2nd slot from sneaking in
            time.sleep(0.03)
            return {"id": f"id-{mid}", "status": "queued", "already_exists": False}

        def get_material(self, material_id: str) -> dict[str, Any]:
            if not release_first.is_set():
                return {
                    "id": material_id,
                    "processing_status": "processing",
                    "processing_stage": "asr",
                }
            with lock:
                active_ids.discard(material_id)
            return {
                "id": material_id,
                "processing_status": "done",
                "title": f"Done {material_id}",
            }

    state = iq.load_state(state_file)
    logger = iq.QueueLogger(log_file, quiet=True)
    result_box: list[Any] = []

    def runner() -> None:
        result_box.append(
            iq.run_queue(
                urls,
                state,
                state_file,
                BackpressureClient(),
                logger,
                concurrency=2,
                max_active=2,
                backpressure_interval=0.02,
                poll_interval=0.02,
                poll_timeout=5.0,
                delay_min=0.0,
                delay_max=0.0,
                stagger_min=0.0,
                stagger_max=0.0,
            )
        )

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    try:
        # Phase 1: Backend has 2 active cards -> script must NOT submit anything
        assert busy_checked_twice.wait(timeout=3.0)
        with lock:
            assert busy_checks >= 2
            assert submitted_urls == []

        # Phase 2: Backend drops to 1 active card -> script must submit EXACTLY 1 URL (never 2!)
        with lock:
            external_active = 1
        assert first_submitted.wait(timeout=3.0)
        time.sleep(0.1)
        with lock:
            assert len(submitted_urls) == 1
            assert external_active + len(active_ids) == 2

        # Phase 3: Free backend -> second URL submits and both finish cleanly
        with lock:
            external_active = 0
        release_first.set()
        t.join(timeout=5.0)
        assert not t.is_alive()
    finally:
        with lock:
            external_active = 0
        release_first.set()
        t.join(timeout=2.0)

    summary = result_box[0]
    assert summary.done == 2
    assert summary.failed == 0
    assert max_observed_active <= 2
    log_text = log_file.read_text(encoding="utf-8")
    assert "BACKPRESSURE active=2 limit=2" in log_text


def test_post_timeout_recovers_existing_material_without_duplicate_or_false_fail():
    url = "https://www.instagram.com/reel/TIMEOUT_RECOVER/"

    class TimeoutRecoverClient:
        def __init__(self) -> None:
            self.enqueue_calls = 0
            self.find_calls = 0
            self.poll_calls = 0

        def get_busy_count(self) -> int:
            return 0

        def enqueue_url(self, u: str) -> dict[str, Any]:
            self.enqueue_calls += 1
            raise RuntimeError("HTTP timeout after 45s")

        def find_material_by_url(self, u: str) -> dict[str, Any] | None:
            self.find_calls += 1
            return {
                "id": "m-recovered",
                "source_url": url,
                "processing_status": "processing",
                "title": None,
            }

        def get_material(self, material_id: str) -> dict[str, Any]:
            self.poll_calls += 1
            return {
                "id": material_id,
                "processing_status": "done",
                "title": "Recovered Reel",
            }

    client = TimeoutRecoverClient()
    res = iq.process_single_url(
        client,
        url,
        poll_interval=0.01,
        max_active=2,
        sleep_fn=lambda _: None,
    )
    assert res["status"] == "done"
    assert res["material_id"] == "m-recovered"
    assert res["title"] == "Recovered Reel"
    assert client.enqueue_calls == 1
    assert client.find_calls == 1
    assert client.poll_calls == 1

