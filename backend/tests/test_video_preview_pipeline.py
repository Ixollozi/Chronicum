from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import pipelines as pipelines_mod
from app.ai import RateLimitError, TranscribeResult
from app.pipelines.media_bundle import MediaBundle
from app.services import media as media_mod


def _settings(threshold_min: int = 10, preview_fraction: float = 0.15) -> SimpleNamespace:
    return SimpleNamespace(
        video_preview_threshold_min=threshold_min,
        video_preview_fraction=preview_fraction,
    )


def _stub_common_pipeline_deps(monkeypatch, tmp_path, *, transcribe_mock: AsyncMock):
    client = SimpleNamespace(transcribe=transcribe_mock)
    monkeypatch.setattr(pipelines_mod, "_whisper_client", AsyncMock(return_value=client))
    monkeypatch.setattr(pipelines_mod, "fetch_youtube_captions", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.services.localize.localize_transcript_to_russian",
        AsyncMock(side_effect=lambda text, **_kw: text),
    )
    monkeypatch.setattr(pipelines_mod, "ocr_frames", AsyncMock(return_value="Preview OCR text"))
    extract_audio_mock = AsyncMock(side_effect=lambda _src, dst: dst)
    extract_segment_mock = AsyncMock(side_effect=lambda _src, _start, _dur, out_path: out_path)
    atlas_mock = AsyncMock(return_value=[])
    keyframes_mock = AsyncMock(return_value=[str(tmp_path / "kf_001.jpg")])

    monkeypatch.setattr(pipelines_mod, "extract_audio", extract_audio_mock)
    monkeypatch.setattr(pipelines_mod, "extract_audio_segment", extract_segment_mock)
    monkeypatch.setattr(pipelines_mod, "build_screen_atlas", atlas_mock)
    monkeypatch.setattr(pipelines_mod, "extract_keyframes", keyframes_mock)
    return extract_audio_mock, extract_segment_mock, atlas_mock, keyframes_mock


@pytest.mark.parametrize("source_type", ["youtube", "instagram", "tiktok"])
async def test_preview_stage_slices_audio_and_frames_for_social_urls(
    source_type, tmp_path, monkeypatch
):
    video = tmp_path / f"{source_type}_long.mp4"
    video.write_bytes(b"video-bytes")

    bundle = MediaBundle(
        title="Long Talk",
        duration=1200.0,
        video_paths=[str(video)],
        caption="Important video description",
    )
    monkeypatch.setattr(pipelines_mod, "download_media_bundle", AsyncMock(return_value=bundle))

    transcribe_mock = AsyncMock(
        return_value=TranscribeResult(text="[0:05] Intro hook", audio_seconds=180.0, language="en")
    )
    extract_audio_mock, extract_segment_mock, atlas_mock, keyframes_mock = _stub_common_pipeline_deps(
        monkeypatch, tmp_path, transcribe_mock=transcribe_mock
    )

    text, title, dur, audio_sec, media_path = await pipelines_mod.extract_text_for_material(
        source_type=source_type,
        source_url=f"https://example.com/{source_type}/123",
        file_path=None,
        note_text=None,
        stage="preview",
        settings=_settings(threshold_min=10, preview_fraction=0.15),
    )

    assert title == "Long Talk"
    assert dur == 1200.0
    assert audio_sec == 180.0
    assert media_path == str(video)
    assert "[0:05] Intro hook" in text
    assert "Important video description" in text
    assert "[On-screen text OCR preview]" in text
    assert "Preview OCR text" in text

    assert extract_segment_mock.await_count == 1
    seg_args = extract_segment_mock.await_args.args
    assert seg_args[1] == 0
    assert seg_args[2] == 180.0
    assert str(seg_args[3]).endswith("_preview.mp3")
    assert str(transcribe_mock.await_args.args[0]).endswith("_preview.mp3")
    assert extract_audio_mock.await_count == 0
    assert atlas_mock.await_count == 0
    assert keyframes_mock.await_count == 1
    assert keyframes_mock.await_args.kwargs["max_frames"] == 6
    assert keyframes_mock.await_args.kwargs["duration_limit"] == 180.0


async def test_preview_stage_short_url_video_runs_full_pipeline(tmp_path, monkeypatch):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"short-video")
    bundle = MediaBundle(title="Short Clip", duration=120.0, video_paths=[str(video)])
    monkeypatch.setattr(pipelines_mod, "download_media_bundle", AsyncMock(return_value=bundle))

    transcribe_mock = AsyncMock(
        return_value=TranscribeResult(text="[0:01] Full short clip", audio_seconds=120.0, language="ru")
    )
    extract_audio_mock, extract_segment_mock, atlas_mock, _ = _stub_common_pipeline_deps(
        monkeypatch, tmp_path, transcribe_mock=transcribe_mock
    )

    text, title, dur, audio_sec, media_path = await pipelines_mod.extract_text_for_material(
        source_type="youtube",
        source_url="https://youtu.be/short123",
        file_path=None,
        note_text=None,
        stage="preview",
        settings=_settings(threshold_min=10, preview_fraction=0.15),
    )

    assert title == "Short Clip"
    assert dur == 120.0
    assert audio_sec == 120.0
    assert media_path == str(video)
    assert "Full short clip" in text
    assert extract_segment_mock.await_count == 0
    assert extract_audio_mock.await_count == 1
    assert atlas_mock.await_count == 1


async def test_preview_stage_probes_duration_when_bundle_duration_missing(tmp_path, monkeypatch):
    video = tmp_path / "no_meta.mp4"
    video.write_bytes(b"video-bytes")
    bundle = MediaBundle(title="No Meta", duration=0.0, video_paths=[str(video)])
    monkeypatch.setattr(pipelines_mod, "download_media_bundle", AsyncMock(return_value=bundle))
    monkeypatch.setattr(pipelines_mod, "probe_duration_seconds", AsyncMock(return_value=900.0))

    transcribe_mock = AsyncMock(
        return_value=TranscribeResult(text="[0:02] Preview slice", audio_seconds=180.0, language="ru")
    )
    extract_audio_mock, extract_segment_mock, atlas_mock, _ = _stub_common_pipeline_deps(
        monkeypatch, tmp_path, transcribe_mock=transcribe_mock
    )

    _, _, dur, audio_sec, _ = await pipelines_mod.extract_text_for_material(
        source_type="tiktok",
        source_url="https://tiktok.com/@u/video/1",
        file_path=None,
        note_text=None,
        stage="preview",
        settings=_settings(threshold_min=10, preview_fraction=0.15),
    )

    assert dur == 900.0
    assert audio_sec == 180.0
    assert extract_segment_mock.await_count == 1
    assert extract_audio_mock.await_count == 0
    assert atlas_mock.await_count == 0


async def test_preview_stage_uploaded_video_does_not_swallow_rate_limit_error(tmp_path, monkeypatch):
    video = tmp_path / "uploaded_long.mp4"
    video.write_bytes(b"video-bytes")
    monkeypatch.setattr(pipelines_mod, "probe_duration_seconds", AsyncMock(return_value=1200.0))

    transcribe_mock = AsyncMock(side_effect=RateLimitError("429 too many requests"))
    extract_audio_mock, extract_segment_mock, atlas_mock, _ = _stub_common_pipeline_deps(
        monkeypatch, tmp_path, transcribe_mock=transcribe_mock
    )

    with pytest.raises(RateLimitError, match="429"):
        await pipelines_mod.extract_text_for_material(
            source_type="video",
            source_url=None,
            file_path=str(video),
            note_text=None,
            stage="preview",
            settings=_settings(threshold_min=10, preview_fraction=0.15),
        )

    assert extract_segment_mock.await_count == 1
    assert transcribe_mock.await_count == 1
    assert extract_audio_mock.await_count == 0
    assert atlas_mock.await_count == 0


async def test_preview_stage_segment_extraction_failure_falls_back_cleanly(tmp_path, monkeypatch):
    video = tmp_path / "weird_codec.mp4"
    video.write_bytes(b"video-bytes")
    monkeypatch.setattr(pipelines_mod, "probe_duration_seconds", AsyncMock(return_value=1200.0))

    transcribe_mock = AsyncMock(
        return_value=TranscribeResult(text="Recovered text", audio_seconds=200.0, language="ru")
    )
    extract_audio_mock, extract_segment_mock, _, _ = _stub_common_pipeline_deps(
        monkeypatch, tmp_path, transcribe_mock=transcribe_mock
    )
    extract_segment_mock.side_effect = RuntimeError("ffmpeg segment failed")

    text, _, dur, _, _ = await pipelines_mod.extract_text_for_material(
        source_type="video",
        source_url=None,
        file_path=str(video),
        note_text=None,
        stage="preview",
        settings=_settings(threshold_min=10, preview_fraction=0.15),
    )

    assert "Recovered text" in text
    assert dur == 1200.0
    assert extract_segment_mock.await_count == 1
    assert extract_audio_mock.await_count == 1
    assert transcribe_mock.await_count == 1


async def test_preview_stage_multi_video_carousel_respects_budget(tmp_path, monkeypatch):
    v1 = tmp_path / "slide1.mp4"
    v2 = tmp_path / "slide2.mp4"
    v1.write_bytes(b"v1")
    v2.write_bytes(b"v2")

    bundle = MediaBundle(
        title="Carousel",
        duration=0.0,
        video_paths=[str(v1), str(v2)],
        caption="Carousel caption",
    )
    monkeypatch.setattr(pipelines_mod, "download_media_bundle", AsyncMock(return_value=bundle))
    monkeypatch.setattr(pipelines_mod, "probe_duration_seconds", AsyncMock(return_value=400.0))

    transcribe_mock = AsyncMock(
        return_value=TranscribeResult(text="Slide 1 preview", audio_seconds=180.0, language="ru")
    )
    extract_audio_mock, extract_segment_mock, atlas_mock, _ = _stub_common_pipeline_deps(
        monkeypatch, tmp_path, transcribe_mock=transcribe_mock
    )

    text, _, dur, audio_sec, media_path = await pipelines_mod.extract_text_for_material(
        source_type="instagram",
        source_url="https://instagram.com/p/carousel1/",
        file_path=None,
        note_text=None,
        stage="preview",
        settings=_settings(threshold_min=10, preview_fraction=0.15),
    )

    assert dur == 800.0
    assert audio_sec == 180.0
    assert media_path == str(v1)
    assert "[Видео 1]" in text
    assert "[Видео 2]" not in text
    assert "Carousel caption" in text
    assert extract_segment_mock.await_count == 1
    assert transcribe_mock.await_count == 1
    assert extract_audio_mock.await_count == 0
    assert atlas_mock.await_count == 0


async def test_full_stage_falls_back_to_local_file_when_download_fails(tmp_path, monkeypatch):
    local_video = tmp_path / "already_downloaded.mp4"
    local_video.write_bytes(b"local-mp4-data")

    monkeypatch.setattr(
        pipelines_mod,
        "download_media_bundle",
        AsyncMock(side_effect=RuntimeError("yt-dlp rate limited")),
    )
    monkeypatch.setattr(pipelines_mod, "probe_duration_seconds", AsyncMock(return_value=1200.0))

    transcribe_mock = AsyncMock(
        return_value=TranscribeResult(text="[0:00] Full local transcript", audio_seconds=1200.0, language="ru")
    )
    extract_audio_mock, extract_segment_mock, atlas_mock, _ = _stub_common_pipeline_deps(
        monkeypatch, tmp_path, transcribe_mock=transcribe_mock
    )

    text, _, dur, audio_sec, media_path = await pipelines_mod.extract_text_for_material(
        source_type="youtube",
        source_url="https://youtu.be/abc123",
        file_path=str(local_video),
        note_text=None,
        stage="full",
        settings=_settings(threshold_min=10, preview_fraction=0.15),
    )

    assert "Full local transcript" in text
    assert dur == 1200.0
    assert audio_sec == 1200.0
    assert media_path == str(local_video)
    assert extract_audio_mock.await_count == 1
    assert extract_segment_mock.await_count == 0
    assert atlas_mock.await_count == 1


async def test_extract_keyframes_passes_duration_limit_flag(tmp_path, monkeypatch):
    recorded_cmds: list[list[str]] = []

    async def _fake_run(cmd, timeout=600):
        recorded_cmds.append(list(cmd))
        out_dir = Path(cmd[-1]).parent
        (out_dir / "frame_0001.jpg").write_bytes(b"jpg")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(media_mod, "run_cmd", _fake_run)
    frames = await media_mod.extract_keyframes(
        str(tmp_path / "v.mp4"),
        str(tmp_path / "frames"),
        every_seconds=45,
        max_frames=6,
        duration_limit=180.0,
    )

    assert len(frames) == 1
    assert len(recorded_cmds) == 1
    cmd = recorded_cmds[0]
    assert "-t" in cmd
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == "180.0"
    assert t_idx < cmd.index("-vf")
