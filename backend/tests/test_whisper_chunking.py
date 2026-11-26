from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai import GroqTranscribeClient, RateLimitError
from app.services import media as media_mod


class _FakeStatusError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def _make_client(*, max_file_mb: float = 20.0, chunk_seconds: int = 600, max_retries: int = 3):
    settings = SimpleNamespace(
        groq_whisper_model="whisper-large-v3-turbo",
        whisper_max_file_mb=max_file_mb,
        whisper_chunk_seconds=chunk_seconds,
        max_api_retries=max_retries,
    )
    client = GroqTranscribeClient(settings, api_key="test-key")
    return client


def _resp(*, text: str, duration: float, language: str = "ru", segments: list[dict] | None = None):
    return SimpleNamespace(
        text=text,
        duration=duration,
        language=language,
        segments=segments or [],
    )


async def test_small_audio_uses_single_request(tmp_path, monkeypatch):
    audio = tmp_path / "small.mp3"
    audio.write_bytes(b"small-mp3-data")

    monkeypatch.setattr(media_mod, "probe_duration_seconds", AsyncMock(return_value=12.5))
    split_spy = AsyncMock(side_effect=AssertionError("split_audio_chunks must not run for small audio"))
    monkeypatch.setattr(media_mod, "split_audio_chunks", split_spy)

    client = _make_client(max_file_mb=20.0, chunk_seconds=600)
    create_mock = AsyncMock(
        return_value=_resp(
            text="Привет мир",
            duration=12.5,
            language="ru",
            segments=[{"start": 0.0, "end": 4.2, "text": "Привет мир"}],
        )
    )
    client.client.audio.transcriptions.create = create_mock

    res = await client.transcribe(str(audio), language="ru")

    assert create_mock.await_count == 1
    assert res.text == "[0:00] Привет мир"
    assert res.audio_seconds == 12.5
    assert res.language == "ru"
    assert len(res.segments) == 1
    assert split_spy.await_count == 0


async def test_oversized_audio_chunks_and_offsets_timestamps(tmp_path, monkeypatch):
    audio = tmp_path / "huge.mp3"
    audio.write_bytes(b"x" * 4096)  # > 0.001 MB (~1048 bytes)

    created_dirs: list[Path] = []

    async def _fake_split(_src, out_dir, *, chunk_seconds=600, **_kw):
        d = Path(out_dir)
        created_dirs.append(d)
        paths = []
        for i in range(3):
            p = d / f"chunk_{i:03d}.mp3"
            p.write_bytes(b"chunk")
            paths.append(str(p))
        return paths

    chunk_durations = {"chunk_000.mp3": 60.0, "chunk_001.mp3": 60.0, "chunk_002.mp3": 30.0}
    monkeypatch.setattr(
        media_mod,
        "probe_duration_seconds",
        AsyncMock(side_effect=lambda p: chunk_durations.get(Path(p).name, 150.0)),
    )
    monkeypatch.setattr(media_mod, "split_audio_chunks", _fake_split)

    client = _make_client(max_file_mb=0.001, chunk_seconds=600)
    create_mock = AsyncMock(
        side_effect=[
            _resp(
                text="Part one",
                duration=60.0,
                language="en",
                segments=[{"start": 0.0, "end": 10.0, "text": "Part one"}],
            ),
            _resp(
                text="Part two",
                duration=60.0,
                language="en",
                segments=[{"start": 5.0, "end": 15.0, "text": "Part two"}],
            ),
            _resp(
                text="Part three",
                duration=30.0,
                language="en",
                segments=[{"start": 2.0, "end": 8.0, "text": "Part three"}],
            ),
        ]
    )
    client.client.audio.transcriptions.create = create_mock

    res = await client.transcribe(str(audio), language="auto")

    assert create_mock.await_count == 3
    assert res.audio_seconds == 150.0
    assert res.language == "en"
    assert [(s.start, s.end, s.text) for s in res.segments] == [
        (0.0, 10.0, "Part one"),
        (65.0, 75.0, "Part two"),
        (122.0, 128.0, "Part three"),
    ]
    assert "[0:00] Part one" in res.text
    assert "[1:05] Part two" in res.text
    assert "[2:02] Part three" in res.text
    # Temporary chunk directory must be cleaned up automatically
    assert created_dirs and not created_dirs[0].exists()


async def test_long_duration_small_file_triggers_chunking(tmp_path, monkeypatch):
    audio = tmp_path / "long_low_bitrate.ogg"
    audio.write_bytes(b"tiny-bytes")

    probe_values = iter([1500.0, 600.0, 600.0, 300.0])
    monkeypatch.setattr(
        media_mod,
        "probe_duration_seconds",
        AsyncMock(side_effect=lambda _p: next(probe_values)),
    )

    split_calls: list[int] = []

    async def _fake_split(_src, out_dir, *, chunk_seconds=600, **_kw):
        split_calls.append(chunk_seconds)
        d = Path(out_dir)
        out = []
        for i in range(3):
            p = d / f"chunk_{i:03d}.mp3"
            p.write_bytes(b"ok")
            out.append(str(p))
        return out

    monkeypatch.setattr(media_mod, "split_audio_chunks", _fake_split)

    client = _make_client(max_file_mb=20.0, chunk_seconds=600)
    create_mock = AsyncMock(
        side_effect=[
            _resp(
                text=f"Chunk {i}",
                duration=d,
                segments=[{"start": 1.0, "end": 2.0, "text": f"Chunk {i}"}],
            )
            for i, d in enumerate([600.0, 600.0, 300.0], 1)
        ]
    )
    client.client.audio.transcriptions.create = create_mock

    res = await client.transcribe(str(audio), language="ru")

    assert split_calls == [600]
    assert create_mock.await_count == 3
    assert [s.start for s in res.segments] == [1.0, 601.0, 1201.0]
    assert res.audio_seconds == 1500.0


async def test_413_payload_too_large_falls_back_to_chunking(tmp_path, monkeypatch):
    audio = tmp_path / "borderline.mp3"
    audio.write_bytes(b"small-on-disk")

    probe_values = iter([300.0, 150.0, 150.0])
    monkeypatch.setattr(
        media_mod,
        "probe_duration_seconds",
        AsyncMock(side_effect=lambda _p: next(probe_values)),
    )

    async def _fake_split(_src, out_dir, *, chunk_seconds=600, **_kw):
        d = Path(out_dir)
        out = []
        for i in range(2):
            p = d / f"chunk_{i:03d}.mp3"
            p.write_bytes(b"small")
            out.append(str(p))
        return out

    monkeypatch.setattr(media_mod, "split_audio_chunks", _fake_split)

    client = _make_client(max_file_mb=20.0, chunk_seconds=600)
    create_mock = AsyncMock(
        side_effect=[
            _FakeStatusError(413, "Request Entity Too Large"),
            _resp(
                text="First half",
                duration=150.0,
                segments=[{"start": 0.0, "end": 5.0, "text": "First half"}],
            ),
            _resp(
                text="Second half",
                duration=150.0,
                segments=[{"start": 3.0, "end": 9.0, "text": "Second half"}],
            ),
        ]
    )
    client.client.audio.transcriptions.create = create_mock

    res = await client.transcribe(str(audio), language="ru")

    # 1 failed 413 single-shot + 2 chunked calls
    assert create_mock.await_count == 3
    assert "[0:00] First half" in res.text
    assert "[2:33] Second half" in res.text


@pytest.mark.parametrize("status_code", [429, 401])
async def test_429_and_401_raise_rate_limit_immediately_without_retries(
    status_code, tmp_path, monkeypatch
):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"mp3-bytes")
    monkeypatch.setattr(media_mod, "probe_duration_seconds", AsyncMock(return_value=10.0))

    client = _make_client(max_file_mb=20.0, chunk_seconds=600)
    create_mock = AsyncMock(side_effect=_FakeStatusError(status_code, f"status {status_code}"))
    client.client.audio.transcriptions.create = create_mock

    with pytest.raises(RateLimitError):
        await client.transcribe(str(audio), language="ru")

    # Must not retry by stripping timestamp_granularities or prompt
    assert create_mock.await_count == 1


async def test_5xx_retries_transient_error_and_succeeds(tmp_path, monkeypatch):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"mp3-bytes")
    monkeypatch.setattr(media_mod, "probe_duration_seconds", AsyncMock(return_value=10.0))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    client = _make_client(max_file_mb=20.0, chunk_seconds=600, max_retries=3)
    create_mock = AsyncMock(
        side_effect=[
            _FakeStatusError(503, "Service Unavailable"),
            _resp(
                text="Recovered",
                duration=10.0,
                segments=[{"start": 0.0, "end": 2.0, "text": "Recovered"}],
            ),
        ]
    )
    client.client.audio.transcriptions.create = create_mock

    res = await client.transcribe(str(audio), language="ru")

    assert create_mock.await_count == 2
    assert res.text == "[0:00] Recovered"


async def test_param_error_strips_timestamp_granularities_and_retries(tmp_path, monkeypatch):
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"mp3-bytes")
    monkeypatch.setattr(media_mod, "probe_duration_seconds", AsyncMock(return_value=10.0))

    client = _make_client(max_file_mb=20.0, chunk_seconds=600)
    seen_kwargs: list[dict] = []

    async def _fake_create(**kwargs):
        seen_kwargs.append(dict(kwargs))
        if "timestamp_granularities" in kwargs:
            raise ValueError("Unsupported parameter: timestamp_granularities")
        return _resp(text="Plain fallback", duration=10.0, segments=[])

    client.client.audio.transcriptions.create = _fake_create
    res = await client.transcribe(str(audio), language="ru")

    assert len(seen_kwargs) == 2
    assert "timestamp_granularities" in seen_kwargs[0]
    assert "timestamp_granularities" not in seen_kwargs[1]
    assert res.text == "Plain fallback"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
async def test_split_audio_chunks_real_ffmpeg_and_probe_duration(tmp_path):
    wav = tmp_path / "sine_12s.wav"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=12",
        str(wav),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    assert proc.returncode == 0 and wav.exists()

    dur = await media_mod.probe_duration_seconds(str(wav))
    assert 11.5 <= dur <= 12.5

    chunks_dir = tmp_path / "chunks"
    chunks = await media_mod.split_audio_chunks(str(wav), str(chunks_dir), chunk_seconds=5)
    assert len(chunks) == 3

    total = 0.0
    for ch in chunks:
        p = Path(ch)
        assert p.exists() and p.stat().st_size > 0
        total += await media_mod.probe_duration_seconds(ch)

    assert 11.5 <= total <= 12.6


async def test_probe_duration_ffmpeg_fallback_when_ffprobe_unavailable(monkeypatch):
    calls: list[list[str]] = []

    async def _fake_run_cmd(cmd, timeout=60):
        calls.append(list(cmd))
        if len(calls) == 1:
            return SimpleNamespace(returncode=127, stdout="", stderr="ffprobe: not found")
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="  Duration: 00:12:34.50, start: 0.000000, bitrate: 128 kb/s\n",
        )

    monkeypatch.setattr(media_mod, "run_cmd", _fake_run_cmd)
    dur = await media_mod.probe_duration_seconds("/tmp/fake_audio.mp3")
    assert dur == pytest.approx(12 * 60 + 34.5, rel=1e-4)
    assert len(calls) == 2
