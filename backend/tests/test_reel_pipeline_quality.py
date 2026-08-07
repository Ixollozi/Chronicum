"""Unit tests for reel pipeline quality helpers (free, no API)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.schemas import KnowledgeCard, UsefulnessSignals  # noqa: E402
from app.services.moments import (  # noqa: E402
    build_listicle_picks,
    detect_listicle,
    is_ocr_junk,
    parse_timed_segments,
)
from app.services.resources import (  # noqa: E402
    digest_lines,
    harvest_resources,
    jaccard,
    merge_card_resources,
    normalize_asr_glitches,
    token_set,
)
from app.services.screen_atlas import (  # noqa: E402
    AtlasFrame,
    format_timed_ocr_block,
    parse_timed_ocr_block,
)


def test_parse_timed_ocr_block():
    raw = """
[0:00] speech hello
[On-screen OCR timed]
[OCR 0:14] Premiere Pro DaVinci
[OCR 0:24] #4 музыка
[OCR 0:37] ДЛЯ СТОКОВЫХ Envato Storyblocks Pexels
"""
    frames = parse_timed_ocr_block(raw)
    assert len(frames) == 3
    assert frames[0].t_sec == 14
    assert "Premiere" in frames[0].text
    assert frames[1].t_sec == 24
    block = format_timed_ocr_block(frames)
    assert "[On-screen OCR timed]" in block
    assert "[OCR 0:37]" in block


def test_harvest_brands_and_links():
    raw = """
Шаг 1 Premiere Pro
[On-screen OCR timed]
[OCR 0:37] Envato Elements Storyblocks Pexels
и чат-жпти для треков
"""
    fixed = normalize_asr_glitches(raw)
    assert "ChatGPT" in fixed
    h = harvest_resources(fixed)
    assert "Adobe Premiere Pro" in h.tools
    assert "Pexels" in h.tools
    assert "Storyblocks" in h.tools
    assert any("pexels" in str(x.get("url") or "").lower() for x in h.links)
    tools, links = merge_card_resources(["CapCut"], [], h)
    assert "CapCut" in tools
    assert "Adobe Premiere Pro" in tools
    assert links
    assert all(isinstance(x, dict) and x.get("url") for x in links)


def test_card_dedupe_key_ideas():
    card = KnowledgeCard(
        title="7 шагов монтажа",
        summary="Автор даёт обзор рабочих шагов монтажа коротких роликов и призыв в конце.",
        main_idea="Монтаж можно упростить до понятного чеклиста.",
        digest="Выбери Premiere\nРежь паузы AI\nДобавь музыку\nB-roll из Pexels",
        key_ideas=[
            "Выбери Premiere",
            "Режь паузы AI",
            "Удержание внимания важнее монтажа",
        ],
        practical_takeaways=["Ищи B-roll на Pexels"],
        tools=["Adobe Premiere Pro"],
        technologies=[],
        links=[],
        category="",
        tags=["монтаж"],
        usefulness_signals=UsefulnessSignals(
            actionability=3, novelty=2, specificity=3, reuse_likelihood=3, personal_fit=3
        ),
        usefulness_score=8,
        usefulness_reason="listicle ok",
    )
    # Overlapping ideas dropped; principles filled
    assert "Выбери Premiere" not in card.key_ideas
    assert len(card.key_ideas) >= 2
    dig = set().union(*(token_set(x) for x in digest_lines(card.digest)))
    for idea in card.key_ideas:
        assert jaccard(token_set(idea), dig) < 0.65 or "вниман" in idea.lower()
    # Score recalibrated downward from model 8
    assert card.usefulness_score <= 7


def test_ocr_junk_and_listicle_match():
    assert is_ocr_junk("НЕ ИСЧЕЗАЛА")
    assert is_ocr_junk("@yurasergienko1")
    assert not is_ocr_junk("#4 музыка")
    assert not is_ocr_junk("ДЛЯ СТОКОВЫХ Envato Storyblocks Pexels")

    atlas = [
        AtlasFrame(14.0, "Premiere Pro DaVinci Final Cut", 50, None),
        AtlasFrame(24.0, "#4 музыка", 40, None),
        AtlasFrame(37.0, "ДЛЯ СТОКОВЫХ Envato Storyblocks Pexels", 80, None),
        AtlasFrame(54.0, "НЕ ИСЧЕЗАЛА", 20, None),
    ]
    segs = [
        {"start": 7.6, "end": 9.0, "text": "Шаг 1. Выбери программу для монтажа."},
        {"start": 13.2, "end": 16.0, "text": "Шаг 2. Удаляй паузы"},
        {"start": 24.7, "end": 26.0, "text": "Шаг 4. Музыка."},
        {"start": 33.1, "end": 35.0, "text": "Шаг 5. Биролы."},
    ]
    raw = "\n".join(f"[{int(s['start']//60)}:{int(s['start']%60):02d}] {s['text']}" for s in segs)
    raw += "\n" + format_timed_ocr_block(atlas)
    assert detect_listicle(raw, segs, atlas)

    picks = build_listicle_picks(atlas, segs, duration=58.0, max_moments=6)
    captions = " ".join(p["caption"] for p in picks).lower()
    assert "не исчезала" not in captions
    assert any("pexels" in (p.get("caption") or "").lower() or "envato" in (p.get("caption") or "").lower() for p in picks)
    # Should not pair step-2 quote with #4 music visual as single pick identity:
    # each pick caption comes from its OCR frame
    for p in picks:
        cap = (p.get("caption") or "").lower()
        quote = (p.get("quote") or "").lower()
        if "#4" in cap or "музык" in cap:
            assert "шаг 2" not in quote


def test_needs_russian_localization():
    from app.services.localize import looks_like_russian, needs_russian_localization

    ru = "[0:00] Открой CapCut и добавь overlay\n[0:05] Поставь keyframe"
    assert looks_like_russian(ru)
    assert not needs_russian_localization(ru)

    idn = "[0:00] Buka aplikasi CapCut dan tambahkan overlay\n[0:05] Tambah keyframe di sini"
    assert not looks_like_russian(idn)
    assert needs_russian_localization(idn)
    assert needs_russian_localization(idn, language="id")
    assert not needs_russian_localization(idn, language="ru")


def test_ocr_garble_sanitize():
    from app.services.local_ocr import ocr_is_garble, ocr_sanitize

    junk = "HoBbIMOH6oPAMHr pelleHMenpMHecHaGnioaeqke Ao6aBMTbKoHTaKTbl"
    assert ocr_is_garble(junk)
    assert ocr_sanitize(junk) == ""

    mixed = "FACEBOOK KoMaHAapocTa B3AJMTEX Slack 2000"
    cleaned = ocr_sanitize(mixed)
    assert "FACEBOOK" in cleaned.upper() or "facebook" in cleaned.lower()
    assert "Slack" in cleaned or "slack" in cleaned.lower()
    assert "HoBbI" not in cleaned

    good = "Выбери программу для монтажа Premiere Pro"
    assert not ocr_is_garble(good)
    assert "монтажа" in ocr_sanitize(good)


def test_strip_ocr_blocks():
    from app.services.screen_atlas import merge_speech_with_ocr, strip_ocr_blocks, AtlasFrame

    raw = """[0:00] Привет
[0:05] Slack барьер

[On-screen OCR timed]
[OCR 0:10] HoBbIMOH6oPAMHr
[OCR 0:18] FACEBOOK
"""
    speech = strip_ocr_blocks(raw)
    assert "Привет" in speech
    assert "OCR" not in speech
    merged = merge_speech_with_ocr(
        raw,
        [AtlasFrame(18.0, "FACEBOOK junktext", 10, None)],
    )
    assert "Привет" in merged
    assert "FACEBOOK" in merged
    assert "HoBbI" not in merged

