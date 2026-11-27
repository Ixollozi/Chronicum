from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import pipelines as pipelines_mod
from app.ai import TranscribeResult
from app.pipelines.media_bundle import MediaBundle
from app.pipelines.youtube import (
    YouTubeCaptions,
    _candidate_caption_tracks,
    _match_track_key,
    parse_subtitle_payload,
)


def test_parse_json3_captions_strips_noise_and_deduplicates():
    payload = json.dumps(
        {
            "events": [
                {"tStartMs": 1000, "segs": [{"utf8": "[Music]"}]},
                {"tStartMs": 5000, "segs": [{"utf8": "Hello "}, {"utf8": "world"}]},
                {"tStartMs": 6000, "segs": [{"utf8": "Hello world"}]},
                {"tStartMs": 8000, "segs": [{"utf8": "Hello world how are you"}]},
                {"tStartMs": 12000, "segs": [{"utf8": "Run"}]},
                {"tStartMs": 14000, "segs": [{"utf8": "Running fast today"}]},
            ]
        }
    )
    segs = parse_subtitle_payload(payload, fmt="json3")
    assert segs == [
        (5.0, "Hello world"),
        (8.0, "how are you"),
        (12.0, "Run"),
        (14.0, "Running fast today"),
    ]


def test_parse_vtt_and_srt_captions_cleans_tags_and_rolling_lines():
    vtt = """WEBVTT
Kind: captions
Language: ru

00:00:02.000 --> 00:00:05.000 align:start position:0%
<c>Привет</c> <00:00:03.000><c>всем &amp; добро пожаловать</c>

00:00:05.000 --> 00:00:08.000
Привет всем &amp; добро пожаловать
Сегодня разбираем агентов

00:00:09.000 --> 00:00:11.000
[Музыка]

00:01:05.500 --> 00:01:09.000
Итоговый вывод по архитектуре
"""
    segs = parse_subtitle_payload(vtt, fmt="vtt")
    assert segs == [
        (2.0, "Привет всем & добро пожаловать"),
        (5.0, "Сегодня разбираем агентов"),
        (65.5, "Итоговый вывод по архитектуре"),
    ]

    srt = """1
00:00:01,200 --> 00:00:04,000
<i>Первая строка</i>

2
00:02:10,000 --> 00:02:14,500
Вторая строка
"""
    assert parse_subtitle_payload(srt, fmt="srt") == [
        (1.2, "Первая строка"),
        (130.0, "Вторая строка"),
    ]


def test_candidate_caption_tracks_prefers_manual_over_auto_and_json3():
    info = {
        "language": "de",
        "subtitles": {
            "en-US": [
                {"ext": "vtt", "url": "https://sub/en.vtt"},
                {"ext": "json3", "url": "https://sub/en.json3"},
            ],
            "de": [{"ext": "vtt", "url": "https://sub/de.vtt"}],
        },
        "automatic_captions": {
            "ru": [{"ext": "json3", "url": "https://auto/ru.json3"}],
            "en-orig": [{"ext": "json3", "url": "https://auto/en.json3"}],
        },
    }
    tracks = _candidate_caption_tracks(info, languages=("ru", "en"))
    assert [(lang, is_auto, tr["url"]) for lang, is_auto, tr in tracks] == [
        ("en", False, "https://sub/en.json3"),
        ("de", False, "https://sub/de.vtt"),
        ("ru", True, "https://auto/ru.json3"),
        ("en", True, "https://auto/en.json3"),
    ]


def test_match_track_key_and_candidates_prefer_orig_for_auto_captions():
    auto_tracks = {
        "en": [{"ext": "json3", "url": "https://auto/en-trans.json3"}],
        "ru": [{"ext": "json3", "url": "https://auto/ru-from-en.json3"}],
        "en-US-orig": [{"ext": "json3", "url": "https://auto/en-us-orig.json3"}],
        "ru-orig": [{"ext": "json3", "url": "https://auto/ru-orig.json3"}],
    }
    assert _match_track_key(auto_tracks, "ru", prefer_orig=True) == "ru-orig"
    assert _match_track_key(auto_tracks, "en", prefer_orig=True) == "en-US-orig"
    # Manual mode keeps exact match priority
    assert _match_track_key(auto_tracks, "ru", prefer_orig=False) == "ru"
    # Fallback when -orig is absent
    assert _match_track_key({"ru": auto_tracks["ru"]}, "ru", prefer_orig=True) == "ru"

    info = {
        "language": "en-US",
        "subtitles": {},
        "automatic_captions": auto_tracks,
    }
    tracks = _candidate_caption_tracks(info, languages=("ru", "en"))
    assert [(lang, is_auto, tr["url"]) for lang, is_auto, tr in tracks] == [
        ("ru", True, "https://auto/ru-orig.json3"),
        ("en", True, "https://auto/en-us-orig.json3"),
    ]


def test_captions_format_text_supports_max_seconds():
    caps = YouTubeCaptions(
        title="Long Video",
        duration=1800.0,
        language="ru",
        is_auto=False,
        segments=[
            (10.0, "Вступление"),
            (120.0, "Основная часть"),
            (400.0, "Дальше в ролике"),
            (3665.0, "Финал через час"),
        ],
    )
    preview = caps.format_text(max_seconds=200.0)
    assert "[0:10] Вступление" in preview
    assert "[2:00] Основная часть" in preview
    assert "Дальше в ролике" not in preview

    full = caps.format_text()
    assert "[1:01:05] Финал через час" in full


async def test_youtube_captions_used_first_with_zero_whisper_seconds(monkeypatch):
    caps = YouTubeCaptions(
        title="YouTube Lecture",
        duration=1200.0,
        language="en",
        is_auto=False,
        segments=[
            (5.0, "Intro to structured agents and tool calling in production systems."),
            (120.0, "First key pattern for reliable tool execution."),
            (600.0, "Deep dive into memory compaction and evaluation loops."),
        ],
        description="Video links: https://example.com/docs",
    )
    fetch_mock = AsyncMock(return_value=caps)
    download_mock = AsyncMock()
    whisper_mock = AsyncMock()
    localize_mock = AsyncMock(side_effect=lambda text, **_kw: f"[RU] {text}")

    monkeypatch.setattr(pipelines_mod, "fetch_youtube_captions", fetch_mock)
    monkeypatch.setattr(pipelines_mod, "download_media_bundle", download_mock)
    monkeypatch.setattr(pipelines_mod, "_whisper_client", whisper_mock)
    monkeypatch.setattr("app.services.localize.localize_transcript_to_russian", localize_mock)

    settings = SimpleNamespace(
        youtube_prefer_captions=True,
        video_preview_threshold_min=10,
        video_preview_fraction=0.2,
    )

    # Preview stage slices captions to first 240s (20% of 1200s) and uses 0 Whisper seconds
    text_p, title_p, dur_p, audio_p, media_p = await pipelines_mod.extract_text_for_material(
        source_type="youtube",
        source_url="https://youtu.be/demo123",
        file_path=None,
        note_text=None,
        stage="preview",
        settings=settings,
    )
    assert title_p == "YouTube Lecture"
    assert dur_p == 1200.0
    assert audio_p == 0.0
    assert media_p is None
    assert "Intro to structured agents" in text_p
    assert "First key pattern" in text_p
    assert "Deep dive into memory" not in text_p
    assert "Video links: https://example.com/docs" in text_p
    assert download_mock.await_count == 0
    assert whisper_mock.await_count == 0

    # Full stage gets all captions, still 0 Whisper seconds
    text_f, _, _, audio_f, _ = await pipelines_mod.extract_text_for_material(
        source_type="youtube",
        source_url="https://youtu.be/demo123",
        file_path=None,
        note_text=None,
        stage="full",
        settings=settings,
    )
    assert audio_f == 0.0
    assert "Deep dive into memory" in text_f
    assert download_mock.await_count == 0


async def test_youtube_falls_back_to_whisper_when_captions_unavailable_or_disabled(
    tmp_path, monkeypatch
):
    video = tmp_path / "yt.mp4"
    video.write_bytes(b"mp4-bytes")
    bundle = MediaBundle(title="No Subs Video", duration=90.0, video_paths=[str(video)])

    download_mock = AsyncMock(return_value=bundle)
    transcribe_mock = AsyncMock(
        return_value=TranscribeResult(text="[0:01] Transcribed via Whisper", audio_seconds=90.0, language="ru")
    )
    monkeypatch.setattr(pipelines_mod, "download_media_bundle", download_mock)
    monkeypatch.setattr(
        pipelines_mod,
        "_whisper_client",
        AsyncMock(return_value=SimpleNamespace(transcribe=transcribe_mock)),
    )
    monkeypatch.setattr(
        "app.services.localize.localize_transcript_to_russian",
        AsyncMock(side_effect=lambda text, **_kw: text),
    )
    monkeypatch.setattr(pipelines_mod, "extract_audio", AsyncMock(side_effect=lambda _s, d: d))
    monkeypatch.setattr(pipelines_mod, "build_screen_atlas", AsyncMock(return_value=[]))

    # Case 1: captions return None -> fallback to Whisper
    monkeypatch.setattr(pipelines_mod, "fetch_youtube_captions", AsyncMock(return_value=None))
    text, title, dur, audio_sec, media_path = await pipelines_mod.extract_text_for_material(
        source_type="youtube",
        source_url="https://youtu.be/nosubs",
        file_path=None,
        note_text=None,
        stage="full",
        settings=SimpleNamespace(
            youtube_prefer_captions=True,
            video_preview_threshold_min=10,
            video_preview_fraction=0.2,
        ),
    )
    assert title == "No Subs Video"
    assert dur == 90.0
    assert audio_sec == 90.0
    assert media_path == str(video)
    assert "Transcribed via Whisper" in text
    assert download_mock.await_count == 1

    # Case 2: youtube_prefer_captions=False -> skips caption fetch completely
    fetch_mock = AsyncMock()
    monkeypatch.setattr(pipelines_mod, "fetch_youtube_captions", fetch_mock)
    await pipelines_mod.extract_text_for_material(
        source_type="youtube",
        source_url="https://youtu.be/nosubs",
        file_path=None,
        note_text=None,
        stage="full",
        settings=SimpleNamespace(
            youtube_prefer_captions=False,
            video_preview_threshold_min=10,
            video_preview_fraction=0.2,
        ),
    )
    assert fetch_mock.await_count == 0
    assert download_mock.await_count == 2
