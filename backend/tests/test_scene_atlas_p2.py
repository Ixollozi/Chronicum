"""Tests for P2.1: ffmpeg scene-change detection, timestamp merging, and bounded parallel OCR."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import media, screen_atlas


@pytest.mark.asyncio
async def test_detect_scene_timestamps_parses_filters_and_downsamples(tmp_path: Path, monkeypatch):
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"fake-video")

    # Simulate ffmpeg showinfo stderr with clustered and edge timestamps
    fake_stderr = "\n".join(
        [
            "[Parsed_showinfo_1 @ 0x1] n:0 pts:10 pts_time:0.05 pos:100",
            "[Parsed_showinfo_1 @ 0x1] n:1 pts:20 pts_time:2.10 pos:200",
            "[Parsed_showinfo_1 @ 0x1] n:2 pts:25 pts_time:2.60 pos:250",  # within min_gap=1.5 of 2.10
            "[Parsed_showinfo_1 @ 0x1] n:3 pts:50 pts_time:5.25 pos:500",
            "[Parsed_showinfo_1 @ 0x1] n:4 pts:90 pts_time:9.40 pos:900",
            "[Parsed_showinfo_1 @ 0x1] n:5 pts:140 pts_time:14.00 pos:1400",
            "[Parsed_showinfo_1 @ 0x1] n:6 pts:180 pts_time:18.50 pos:1800",
            "[Parsed_showinfo_1 @ 0x1] n:7 pts:199 pts_time:19.95 pos:1990",  # near duration=20.0
        ]
    )

    async def fake_run_cmd(cmd, timeout=300):
        assert any("gt(scene,0.30)" in part for part in cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr=fake_stderr)

    monkeypatch.setattr(media, "run_cmd", fake_run_cmd)

    ts_all = await media.detect_scene_timestamps(
        str(video), threshold=0.30, max_scenes=10, min_gap=1.5, duration=20.0
    )
    assert ts_all == [2.1, 5.25, 9.4, 14.0, 18.5]

    ts_capped = await media.detect_scene_timestamps(
        str(video), threshold=0.30, max_scenes=3, min_gap=1.5, duration=20.0
    )
    assert len(ts_capped) == 3
    assert ts_capped[0] == 2.1
    assert ts_capped[-1] == 18.5


def test_merge_atlas_timestamps_prioritizes_scenes_and_bounds_total():
    grid = [0.4, 2.4, 4.4, 6.4, 8.4, 10.4]
    scenes = [2.1, 6.9, 9.8]
    merged = screen_atlas.merge_atlas_timestamps(grid, scenes, min_gap=1.0, max_total=6)
    # Scene timestamps win over nearby grid ticks (2.1 replaces 2.4, 6.9 replaces 6.4, 9.8 replaces 10.4)
    assert 2.1 in merged
    assert 6.9 in merged
    assert 9.8 in merged
    assert 2.4 not in merged
    assert len(merged) <= 6
    assert merged == sorted(merged)


@pytest.mark.asyncio
async def test_build_screen_atlas_parallel_ocr_and_no_double_ocr(tmp_path: Path, monkeypatch):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"fake-video")
    atlas_dir = tmp_path / "atlas"

    monkeypatch.setattr(screen_atlas, "probe_duration_seconds", lambda _p: asyncio.sleep(0, result=20.0))

    async def fake_detect(vpath, **kwargs):
        return [3.1, 9.7]

    async def fake_extract(vpath, t_sec, out_path):
        Path(out_path).write_bytes(b"jpg-bytes")
        return True

    in_flight = 0
    max_in_flight = 0
    ocr_calls = 0
    scored_with_ocr_text: list[str | None] = []

    topics = [
        "Архитектура базы данных",
        "Индексы и планы запросов",
        "Очереди задач и воркеры",
        "Кэширование ответов",
        "Настройка авторизации",
        "Мониторинг и метрики",
        "Деплой контейнеров",
        "Резервное копирование",
        "Профилирование памяти",
        "Интеграция вебхуков",
        "Нагрузочное тестирование",
        "Аудит безопасности",
    ]

    def fake_ocr(path: str) -> str:
        nonlocal in_flight, max_in_flight, ocr_calls
        import time

        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        call_n = ocr_calls + 1
        ocr_calls = call_n
        time.sleep(0.03)
        in_flight -= 1
        topic = topics[(call_n - 1) % len(topics)]
        return f"Слайд {call_n}: {topic}"

    def fake_score(path: str, *, ocr_text: str | None = None) -> float:
        scored_with_ocr_text.append(ocr_text)
        return 42.0

    monkeypatch.setattr(screen_atlas, "detect_scene_timestamps", fake_detect)
    monkeypatch.setattr(screen_atlas, "extract_frame_at", fake_extract)
    monkeypatch.setattr(screen_atlas, "_ocr_image", fake_ocr)
    monkeypatch.setattr(screen_atlas, "score_frame_file", fake_score)

    frames = await screen_atlas.build_screen_atlas(
        str(video),
        atlas_dir,
        duration=20.0,
        source_type="youtube",
        keep_top_n=12,
        scene_detect=True,
        ocr_concurrency=2,
    )

    assert len(frames) >= 2
    assert any(abs(f.t_sec - 3.1) < 0.01 for f in frames)
    assert any(abs(f.t_sec - 9.7) < 0.01 for f in frames)
    # Bounded concurrency was respected and actually overlapped
    assert max_in_flight <= 2
    assert max_in_flight >= 2
    # score_frame_file received pre-computed OCR text on every call (no double Tesseract pass)
    assert len(scored_with_ocr_text) == ocr_calls
    assert all(txt and "Слайд" in txt for txt in scored_with_ocr_text)
    assert (atlas_dir / "atlas.json").is_file()


def test_atlas_sampling_covers_full_long_video_duration():
    every, max_frames = screen_atlas.atlas_sampling(duration=1800.0, source_type="youtube")
    assert max_frames == 36
    assert every * max_frames >= 1799.0
    assert every >= 50.0

    every_short, max_short = screen_atlas.atlas_sampling(duration=60.0, source_type="youtube")
    assert every_short == 2.0
    assert max_short <= 31


@pytest.mark.asyncio
async def test_run_cmd_sets_create_no_window_on_win32(monkeypatch):
    import subprocess

    captured: list[dict] = []

    def fake_run(args, **kwargs):
        captured.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    monkeypatch.setattr(media.sys, "platform", "win32")
    monkeypatch.setattr(media.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    res = await media.run_cmd(["ffmpeg", "-version"], timeout=10)
    assert res.returncode == 0
    assert captured[-1].get("creationflags") == 0x08000000

    monkeypatch.setattr(media.sys, "platform", "linux")
    await media.run_cmd(["ffmpeg", "-version"], timeout=10)
    assert "creationflags" not in captured[-1]
