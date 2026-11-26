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
    score_atlas_frame,
)
from app.services.resources import (  # noqa: E402
    digest_lines,
    extract_github_slugs,
    harvest_resources,
    jaccard,
    merge_card_resources,
    normalize_asr_glitches,
    normalize_brand_names,
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
    # No brand→URL generation: a named site is not a link until literally shown
    assert not any("pexels" in str(x.get("url") or "").lower() for x in h.links)
    tools, links = merge_card_resources(["CapCut"], [], h, raw_text=fixed)
    # CapCut is LLM-only with no speech/OCR evidence → dropped
    assert "CapCut" not in tools
    assert "Adobe Premiere Pro" in tools
    assert "Pexels" in tools
    assert links == []


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

[Слайд 1]
[OCR]
Fork You Code Watch MIT License latent-spaces/brag

[Слайд 2]
Чистый слайд про CapCut

[On-screen OCR timed]
[OCR 0:10] HoBbIMOH6oPAMHr
[OCR 0:18] FACEBOOK
"""
    speech = strip_ocr_blocks(raw)
    assert "Привет" in speech
    assert "OCR" not in speech
    assert "Fork You" not in speech
    assert "CapCut" in speech
    merged = merge_speech_with_ocr(
        raw,
        [AtlasFrame(18.0, "FACEBOOK junktext", 10, None)],
    )
    assert "Привет" in merged
    assert "FACEBOOK" in merged
    assert "HoBbI" not in merged


def test_githubradar_asr_and_slug_harvest():
    fixture = Path(__file__).parent / "fixtures" / "githubradar" / "transcript.txt"
    raw = fixture.read_text(encoding="utf-8")
    fixed = normalize_asr_glitches(raw)
    assert "Rybbit" in fixed
    assert "witr" in fixed.lower()
    assert "Rybit" not in fixed
    assert "Vitter" not in fixed

    h = harvest_resources(fixed, duration_seconds=55.0)
    urls = " ".join(str(x.get("url") or "") for x in h.links).lower()
    assert "github.com/rybbit-io/rybbit" in urls
    assert "github.com/boardui/boardui" in urls
    assert "github.com/pranshuparmar/witr" in urls
    assert "Rybbit" in h.tools or any("rybbit" in t.lower() for t in h.tools)
    # CTA / percent junk should not invent fake tools
    assert not any("87%" in t for t in h.tools)
    assert not any("подпис" in t.lower() for t in h.tools)

    assert "Rybbit" in normalize_brand_names("Rybit analytics")
    assert "witr" in normalize_brand_names("try Vitter today")


def test_extract_github_slugs_filters_false_positives():
    text = "see http/https and 12/34 and real rybbit-io/rybbit plus Wcrybbit.io noise"
    slugs = extract_github_slugs(text, source="ocr")
    urls = [x["url"].lower() for x in slugs]
    assert any("rybbit-io/rybbit" in u for u in urls)
    assert not any("http/https" in u for u in urls)
    assert not any("12/34" in u for u in urls)


def test_githubradar_moments_prefer_slugs_over_percent():
    import json

    atlas_raw = json.loads(
        (Path(__file__).parent / "fixtures" / "githubradar" / "ocr_atlas.json").read_text(encoding="utf-8")
    )
    atlas = [AtlasFrame(float(x["t_sec"]), x["text"], int(x["score"]), None) for x in atlas_raw]
    segs = [
        {"start": 5.0, "end": 8.0, "text": "Первый проект — Rybbit."},
        {"start": 18.0, "end": 21.0, "text": "Второй проект — BoardUI."},
        {"start": 32.0, "end": 35.0, "text": "Третий проект — witr."},
    ]
    raw = "Три проекта на GitHub. Первый Rybbit. Второй BoardUI. Третий witr."
    assert detect_listicle(raw, segs, atlas)

    pct = next(fr for fr in atlas if "%" in fr.text)
    slug = next(fr for fr in atlas if "rybbit-io/rybbit" in fr.text)
    assert score_atlas_frame(slug, duration=55.0, listicle=True) > score_atlas_frame(
        pct, duration=55.0, listicle=True
    )

    picks = build_listicle_picks(atlas, segs, duration=55.0, max_moments=6)
    blob = " ".join(f"{p.get('caption','')} {p.get('_ocr','')}" for p in picks).lower()
    assert "rybbit" in blob
    assert "witr" in blob or "boardui" in blob
    assert blob.count("%") < 3  # percent-only frame should not dominate


def test_shallow_promo_listicle_score_cap():
    card = KnowledgeCard(
        title="3 open source проекта",
        summary="Автор показывает три репозитория и зовёт в Telegram.",
        main_idea="Подборка GitHub-проектов без пошаговой установки.",
        digest="Rybbit — аналитика\nBoardUI — компоненты\nwitr — утилита Windows",
        key_ideas=["Open source ускоряет продукт"],
        practical_takeaways=["Сохрани ссылки на репо"],
        tools=["Rybbit", "BoardUI", "witr", "GitHub"],
        technologies=[],
        links=[
            {"url": "https://github.com/rybbit-io/rybbit", "title": "rybbit", "source": "ocr"},
            {"url": "https://github.com/BoardUI/boardui", "title": "boardui", "source": "ocr"},
        ],
        category="",
        tags=["github"],
        usefulness_signals=UsefulnessSignals(
            actionability=4, novelty=3, specificity=4, reuse_likelihood=4, personal_fit=5
        ),
        usefulness_score=9,
        usefulness_reason="promo listicle",
    )
    assert card._is_shallow_promo_listicle()
    assert card.usefulness_score <= 7


def test_asr_hallucination_and_weak_speech():
    from app.services.resources import (
        caption_suggests_english,
        is_asr_hallucination,
        is_weak_speech,
        normalize_asr_glitches,
        strip_weak_speech_from_raw,
    )

    hall = "[0:00] Спасибо за просмотр!"
    fixed = normalize_asr_glitches(hall)
    assert "Спасибо за просмотр" not in fixed
    assert is_asr_hallucination(hall) or is_weak_speech(hall)
    assert is_weak_speech("[0:00] Hi")
    assert not is_weak_speech(
        "[0:00] Today I will show three open source tools for analytics and UI kits on GitHub."
    )
    assert caption_suggests_english("Saves me hours #ai #claude #marketing #digitalmarketing")
    assert not caption_suggests_english("Где взять дизайн-систему и шрифты бесплатно")

    raw = "[0:00] Спасибо за просмотр!\n\n[On-screen OCR timed]\n[OCR 0:10] Outlier-app.tryoutlier.com"
    cleaned, weak = strip_weak_speech_from_raw(raw)
    assert weak
    assert "OCR" in cleaned
    assert "Спасибо" not in cleaned


def test_ocr_junk_and_typo_hosts_dropped():
    from app.services.resources import harvest_resources, merge_link_items

    h = harvest_resources(
        "see https://Outlier-app.tryoutlier.com and typo https://Outlier-app.tryoutier.com",
        duration_seconds=20,
    )
    urls = " ".join(x["url"].lower() for x in h.links)
    assert "tryoutlier.com" in urls
    assert "tryoutier.com" not in urls

    merged = merge_link_items(
        [{"url": "https://fonts.google.com", "title": "GF", "source": "ocr"}],
        [{"url": "https://fontz.google.com", "title": "typo", "source": "ocr"}],
    )
    assert len(merged) == 1
    assert "fonts.google.com" in merged[0]["url"]

    # Flattened OCR host → structured host (SEOMonster)
    seo = merge_link_items(
        [{"url": "https://seomontberavansaber.com", "source": "ocr"}],
        [{"url": "https://seomonster.avansaber.com", "source": "ocr"}],
    )
    assert len(seo) == 1
    assert "seomonster.avansaber.com" in seo[0]["url"]
    assert "seomontber" not in seo[0]["url"]


def test_talking_head_score_percent_demote():
    from app.services.moments import score_atlas_frame

    pct = AtlasFrame(10.0, "87% 64% 91% 55% 78%", 40, None)
    brand = AtlasFrame(12.0, "tweakcn.com design system", 70, None)
    assert score_atlas_frame(brand, duration=40.0, listicle=False) > score_atlas_frame(
        pct, duration=40.0, listicle=False
    )


def test_overlay_brands_and_bare_google_demote():
    import re
    from app.services.resources import extract_overlay_product_names, merge_card_resources

    raw = (
        "[0:00] Повышаешь уровень в видеоиграх? Попробуй улучшить свою игру с Oreo.\n\n"
        "[On-screen OCR timed]\n"
        "[OCR 0:02] Claude Fabel Is Crushing 3D Motion Design\n"
        "[OCR 0:08] Gemini Omni\n"
        "[OCR 0:10] Seedance 2.0\n"
        "[OCR 0:18] google.com\n"
        "[OCR 0:20] Comment Claude\n"
    )
    h = harvest_resources(raw, duration_seconds=30.0)
    tools_l = " ".join(h.tools).lower()
    assert "gemini" in tools_l
    assert "seedance" in tools_l
    assert "claude" in tools_l
    urls = " ".join(x["url"].lower() for x in h.links)
    assert "google.com" not in urls or "/search" in urls  # bare apex dropped

    overlays = extract_overlay_product_names("Gemini Omni\nSeedance 2.0\nComment Claude")
    assert any("Gemini" in o or "gemini" in o.lower() for o in overlays) or any(
        "Seedance" in o for o in overlays
    )

    tools, links = merge_card_resources(
        ["Claude"],
        [{"url": "https://google.com", "title": "Google", "source": "ocr"}],
        h,
    )
    assert not any(urlparse_host(li["url"]) == "google.com" for li in links)
    assert any("Seedance" in t or "Gemini" in t for t in tools)
    assert not any(re.match(r"^OCR\b", t, re.I) for t in tools)


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc.lower().removeprefix("www.")


def test_speech_offtopic_oreo_vs_tutorial():
    from app.services.resources import is_speech_offtopic, is_weak_speech

    tusharx = (
        "[0:00] Повышаешь уровень в видеоиграх? Попробуй улучшить свою игру с Oreo. "
        "Просто поверни, щёлкни, окуни.\n\n"
        "[On-screen OCR timed]\n"
        "[OCR 0:08] Gemini Omni\n"
        "[OCR 0:10] Seedance 2.0\n"
        "[OCR 0:12] Claude Fabel\n"
    )
    assert not is_weak_speech(tusharx)
    assert is_speech_offtopic(tusharx)

    tutorial = (
        "[0:00] Открываем Google Flow, выбираем OmniFlash и загружаем исходное видео. "
        "Вставляем универсальный промпт и запускаем генерацию.\n\n"
        "[On-screen OCR timed]\n"
        "[OCR 0:16] https://labs.google\n"
        "[OCR 0:20] OmniFlash\n"
    )
    assert not is_weak_speech(tutorial)
    assert not is_speech_offtopic(tutorial)


def test_offtopic_digest_grounded_in_tools():
    from app.services.resources import digest_lines, harvest_resources, is_speech_offtopic

    raw = (
        "[0:00] Повышаешь уровень в видеоиграх? Попробуй улучшить свою игру с Oreo.\n\n"
        "[On-screen OCR timed]\n"
        "[OCR 0:08] Gemini Omni\n"
        "[OCR 0:10] Seedance 2.0\n"
    )
    assert is_speech_offtopic(raw)
    h = harvest_resources(raw)
    # Simulate _apply_card post-hoc rebuild
    invented = (
        "- Зарегистрируйтесь в Claude\n"
        "- Примените After Effects\n"
        "- Ищите референсы через Google\n"
        "- Ещё шаг четыре"
    )
    lines = digest_lines(invented)[:3]
    dig = "\n".join(f"- {ln}" for ln in lines)
    dig_l = dig.lower()
    grounded = any(t.lower() in dig_l or t.lower().split()[0] in dig_l for t in h.tools)
    if not grounded and h.tools:
        dig = "\n".join(f"- На экране: {t}" for t in h.tools[:3])
    assert len(digest_lines(dig)) <= 3
    assert "oreo" not in dig.lower()
    assert any(x in dig for x in h.tools[:3])


def test_overlay_frames_not_skipped_as_talking_head():
    """Non-listicle + brand overlays should score as overlay-rich (gate uses this)."""
    from app.services.local_ocr import ocr_is_garble
    from app.services.moments import score_atlas_frame
    from app.services.resources import extract_overlay_product_names

    frames = [
        AtlasFrame(2.0, "Gemini Omni", 80, None),
        AtlasFrame(8.0, "Seedance 2.0", 75, None),
        AtlasFrame(18.0, "google.com", 20, None),
    ]
    usable = sum(1 for a in frames if (a.text or "").strip() and not ocr_is_garble(a.text))
    overlay_n = 0
    for fr in frames:
        text = fr.text or ""
        if ocr_is_garble(text):
            continue
        if extract_overlay_product_names(text) or score_atlas_frame(fr, duration=25.0, listicle=False) > 3.5:
            overlay_n += 1
    # Even if usable_ocr is thin, overlay_n must unlock moments gate
    assert overlay_n >= 1
    assert usable >= 1 or overlay_n >= 1


def test_skillspector_ocr_salvage_and_harvest():
    """NVIDIA SkillSpector tip: keep CamelCase/.zip through garble; map GitHub repo."""
    from app.services.local_ocr import ocr_sanitize
    from app.services.resources import (
        extract_overlay_product_names,
        ground_digest_lines,
        harvest_resources,
        merge_card_resources,
        normalize_asr_glitches,
    )

    mixed = "Download SkillSpector.zip Download 71noAo3pHTeMbHbIM natt eph MOZG Claude"
    cleaned = ocr_sanitize(mixed)
    assert "SkillSpector" in cleaned or "skillspector" in cleaned.lower()
    assert "SkillSpector" in extract_overlay_product_names("SkillSpector.zip")
    assert "SkillSpector" in extract_overlay_product_names("SkillSpector")

    speech = normalize_asr_glitches(
        "[0:00] Проверь скиллы в код-коде. NVIDIA выложила бесплатный сканер с открытым кодом.\n"
        "[0:20] 71 подозрительный паттерн. Гоняй скилл до установки."
    )
    assert "Cursor" in speech
    raw = (
        speech
        + "\n\n[On-screen OCR timed]\n"
        + f"[OCR 0:22] {cleaned}\n"
        + "[OCR 0:26] Claude\n"
    )
    h = harvest_resources(raw, duration_seconds=40)
    tools_l = " ".join(h.tools).lower()
    assert "skillspector" in tools_l or "nvidia" in tools_l
    # No local brand→repo list: harvest must not invent the repo URL.
    # It is resolved online at card-apply time (verify.resolve_github_repo).
    urls = " ".join(x["url"].lower() for x in h.links)
    assert "nvidia/skillspector" not in urls

    tools, _links = merge_card_resources(
        ["Claude", "GitHub"], [], h, raw_text=raw
    )
    tools_l2 = " ".join(tools).lower()
    assert "skillspector" in tools_l2 or "nvidia" in tools_l2
    assert "claude" not in tools_l2  # logo-only OCR, not in speech

    dig = ground_digest_lines(
        "- Скачай SkillSpector с GitHub\n"
        "- При оценке риска отклоняй скрипт\n"
        "- Используй Claude для генерации безопасных альтернатив",
        tools=tools,
        raw_text=raw,
    )
    assert "claude" not in dig.lower()
    # «с GitHub» is not literally in evidence → that bullet is cut by generic
    # grounding; the repo link itself arrives via online resolve at apply time.
    assert "github" not in dig.lower()
    assert "оценке риска" in dig.lower()


def test_garble_tools_and_ungrounded_links_dropped():
    """QA 4-posts regression: OCR soup tools + LLM-invented links must not survive merge."""
    from app.services.resources import harvest_resources, merge_card_resources

    raw = (
        "[0:00] Как попасть в геовыдачу? Расскажу три способа.\n"
        "[0:50] Разместить материалы на площадках вроде Reddit и Wikipedia.\n\n"
        "[On-screen OCR timed]\n"
        "[OCR 0:22] MupyioTcyHaoc BcerAacai\n"
        "[OCR 0:30] HocTblo 80%\n"
    )
    h = harvest_resources(raw, duration_seconds=60)
    tools, links = merge_card_resources(
        ["MupyioTcyHaoc BcerAacai", "HocTblo 80", "Reddit"],
        [
            {"url": "https://www.reddit.com", "title": "Reddit", "source": "llm"},
            {"url": "https://quark.com", "title": "Quark", "source": "llm"},
        ],
        h,
        raw_text=raw,
    )
    blob = " ".join(tools).lower()
    assert "mupyio" not in blob
    assert "hoctblo" not in blob
    urls = " ".join(x["url"] for x in links)
    assert "reddit.com" not in urls
    assert "quark.com" not in urls


def test_evidence_link_kept_and_bare_github_dropped():
    from app.services.resources import harvest_resources, merge_card_resources

    raw = (
        "[0:00] Это называется Logo Design Skill. Поставьте комментарий logo.\n\n"
        "More like this → gittrend.io\n\n"
        "[On-screen OCR timed]\n"
        "[OCR 0:02] GitHub github.com Claude gittrend.io\n"
    )
    h = harvest_resources(raw, duration_seconds=35)
    tools, links = merge_card_resources(
        ["Claude"],
        [{"url": "https://gittrend.io", "title": "GitTrend", "source": "llm"}],
        h,
        raw_text=raw,
    )
    urls = [x["url"] for x in links]
    assert any("gittrend.io" in u for u in urls)
    assert not any(u.rstrip("/") in ("https://github.com", "http://github.com") for u in urls)


def test_negated_tool_is_anti_example():
    from app.services.resources import drop_negated_tools

    raw = (
        "[0:00] Вы можете создавать моушн-графики, не открывая After Effects.\n"
        "[0:08] И нет, я не говорю о After Effects MCP. Это Claude Opus.\n"
        "[0:14] Вместо управления After Effects вы просто говорите Claude, как выглядеть анимации.\n"
        "[0:22] Заменит ли это After Effects? Не совсем.\n"
    )
    out = drop_negated_tools(["After Effects", "Claude"], raw)
    assert "After Effects" not in out
    assert "Claude" in out


def test_digest_drops_invented_brand_steps():
    from app.services.resources import ground_digest_lines

    raw = (
        "[0:00] Говорите Claude, как должна выглядеть анимация, он пишет её кодом.\n"
        "[0:30] Код рендерится кадр за кадром в видео.\n"
    )
    dig = ground_digest_lines(
        "- Опишите анимацию для Claude и получите код\n"
        "- Скопируйте код в p5.js и запустите рендер\n"
        "- Зафиксируйте процесс и ссылки в Notion для команды",
        tools=["Claude"],
        raw_text=raw,
    )
    assert "claude" in dig.lower()
    assert "p5.js" not in dig.lower()
    assert "notion" not in dig.lower()


def test_slug_rejects_and_verified_survives_merge():
    from app.services.resources import (
        _looks_like_github_slug,
        merge_link_items,
    )

    assert not _looks_like_github_slug("and", "or")
    assert not _looks_like_github_slug("github.com", "kaankiziltug")
    assert not _looks_like_github_slug("GitHub-kaankiziltug", "logo")
    assert not _looks_like_github_slug("t.me", "mike_mozg")
    assert _looks_like_github_slug("kaankiziltug", "logo-design-skill")
    assert _looks_like_github_slug("better-auth", "better-icons")

    merged = merge_link_items(
        [{"url": "https://gittrend.io", "title": "", "source": "ocr"}],
        [{"url": "https://gittrend.io", "title": "T", "source": "speech",
          "verified": "yes", "http_status": "200"}],
    )
    assert merged[0]["verified"] == "yes"
    assert merged[0]["http_status"] == "200"


def test_unknown_brand_grounding_without_lists():
    """A product nobody hardcoded passes if the author named it; inventions die."""
    from app.services.resources import harvest_resources, merge_card_resources

    raw = (
        "[0:00] Сегодня покажу ZebraForge — он сам режет паузы и субтитры.\n"
        "[0:20] Открываешь ZebraForge, кидаешь ролик, жмёшь Render.\n"
    )
    h = harvest_resources(raw, duration_seconds=30)
    tools, links = merge_card_resources(
        ["ZebraForge", "QuantumLeap", "Blender"],
        [{"url": "https://quantumleap.example.com", "title": "QL", "source": "llm"}],
        h,
        raw_text=raw,
    )
    assert "ZebraForge" in tools
    assert "QuantumLeap" not in tools
    assert "Blender" not in tools
    assert links == []


def test_digest_generic_grounding_numbers_and_entities():
    """Invented numbers/versions/entities are cut without any brand list."""
    from app.services.resources import ground_digest_lines

    raw = (
        "[0:00] В ZebraForge есть библиотека из 300 пресетов.\n"
        "[0:10] Экспорт занимает 2 минуты.\n"
    )
    dig = ground_digest_lines(
        "- Откройте ZebraForge и выберите пресет из библиотеки в 300 штук\n"
        "- Подключите MegaRender и получите 1400 экспортов за минуту\n"
        "- В ZebraForge 9.9 экспорт занимает 2 минуты",
        tools=["ZebraForge"],
        raw_text=raw,
    )
    assert "300" in dig
    assert "1400" not in dig
    assert "megarender" not in dig.lower()
    assert "9.9" not in dig


def test_pick_repo_match_similarity_gate():
    from app.services.verify import pick_repo_match

    items = [
        {"full_name": "random/skillspector-fork", "stargazers_count": 3},
        {"full_name": "NVIDIA/SkillSpector", "stargazers_count": 900},
        {"full_name": "someone/skill", "stargazers_count": 5000},
    ]
    match = pick_repo_match("SkillSpector", items)
    assert match is not None
    assert match["full_name"] == "NVIDIA/SkillSpector"
    # No plausible match → None, never a guessed repo
    assert pick_repo_match("ZebraForge", [{"full_name": "a/zebra", "stargazers_count": 10}]) is None
    # Short single-token product: «repo containing the word» is not a match
    assert pick_repo_match("NVIDIA", [{"full_name": "NVIDIA/nvidia-docker", "stargazers_count": 9000}]) is None
    # Substring + stars must not beat missing exact (brag-awesome)
    assert (
        pick_repo_match(
            "Brag",
            [
                {"full_name": "someone/brag-awesome", "stargazers_count": 9000},
                {"full_name": "x/bragging-rights", "stargazers_count": 500},
            ],
        )
        is None
    )
    assert (
        pick_repo_match(
            "Brag",
            [{"full_name": "latent-spaces/brag", "stargazers_count": 10}],
        )["full_name"]
        == "latent-spaces/brag"
    )


def test_slide_chrome_not_spoken_and_ocr_tip_cta():
    from app.services.grounding import entity_grounded, GroundingContext, spoken_blob
    from app.services.resources import harvest_resources, ground_digest_lines

    raw = (
        "[0:00] Hello music bed only.\n"
        "[Слайд 1]\n"
        "Fork You Code Iss Create PR MIT License latent-spaces/brag Watch 15\n"
        "[On-screen OCR timed]\n"
        "[OCR 0:05] Comment Claude\n"
        "[OCR 0:08] Gemini Omni\n"
    )
    sp = spoken_blob(raw)
    assert "fork you" not in sp
    assert not entity_grounded("Fork You", GroundingContext.from_raw(raw), blob="spoken")
    h = harvest_resources(raw, duration_seconds=20)
    tools_l = " ".join(h.tools).lower()
    assert "claude" not in tools_l  # Comment Claude CTA
    assert "gemini" in tools_l  # Gemini Omni overlay
    dig = ground_digest_lines(
        "- Напиши ПРОМПТ в комментариях\n- Используй Gemini Omni на экране\n",
        tools=h.tools,
        raw_text=raw,
    )
    assert "промпт" not in dig.lower() or "gemini" in dig.lower()


def test_trust_score_levels():
    from app.services.trust import trust_score

    weak = trust_score(
        raw_text="[0:00] hi\n",
        title="Something",
        tools=["Fork You", "Create PR"],
        links=[],
        digest="",
        evidence=[],
    )
    assert weak.level == "weak"

    ok = trust_score(
        raw_text=(
            "[0:00] Ставим навык Brag из GitHub одной командой и получаем ролик.\n"
            "[0:10] Стоимость около 6 долларов за токены.\n"
            "[On-screen OCR timed]\n"
            "[OCR 0:05] github.com/latent-spaces/brag\n"
        ),
        title="Навык Brag: промо-ролик",
        tools=["Brag"],
        links=[{"url": "https://github.com/latent-spaces/brag", "verified": "yes"}],
        digest="- Запусти brag в корне проекта\n- Проверь стоимость токенов\n",
        evidence=[
            {"kind": "speech", "text": "Ставим навык Brag", "confidence": 0.8},
            {"kind": "ocr", "text": "github.com/latent-spaces/brag", "confidence": 0.7},
        ],
    )
    assert ok.level in {"mcp_safe", "review"}
    assert ok.score >= 0.7


def test_repo_candidates_prefer_product_over_vendor():
    """Title/subject product resolves before a vendor name (NVIDIA case)."""
    from app.services.verify import repo_candidate_names

    names = repo_candidate_names(
        ["NVIDIA", "SkillSpector", "Cursor"],
        title="SkillSpector — сканер от NVIDIA",
    )
    assert names[0] == "SkillSpector"
    # Generic words never become repo queries
    assert "GitHub" not in repo_candidate_names(["GitHub", "yt-dlp"])
    # Title product is a resolve candidate even when tools are OCR chrome
    titled = repo_candidate_names(
        ["Fork You", "Code Iss"],
        title="Навык Brag: промо-ролик",
        raw_text="[0:00] Ставим brag и получаем ролик.\n",
    )
    assert titled[0] == "Brag"


def test_ocr_chrome_not_tools_spoken_only():
    """GitHub UI labels on screen never become tools; speech products do."""
    from app.services.grounding import is_chrome_ocr
    from app.services.resources import ground_digest_lines, merge_card_resources, harvest_resources

    chrome = (
        "GitHub github.com/latent-spaces/brag Code Fork Watch Issues "
        "MIT License Stars 3.6k Create PR"
    )
    assert is_chrome_ocr(chrome)

    raw = (
        "[0:00] Ставим навык Brag — одна команда и готов 20-секундный ролик.\n"
        "[On-screen OCR timed]\n"
        f"[OCR 0:05] {chrome}\n"
    )
    h = harvest_resources(raw, duration_seconds=40)
    tools, links = merge_card_resources(
        ["Brag", "Fork You", "Code Iss", "Create PR", "YOUR ASSISTANT"],
        [],
        h,
        raw_text=raw,
    )
    assert "Brag" in tools
    for junk in ("Fork You", "Code Iss", "Create PR", "YOUR ASSISTANT"):
        assert junk not in tools
    assert any("latent-spaces/brag" in str(li.get("url") or "") for li in links)

    dig = ground_digest_lines(
        "- Кешируй данные через Redis и RabbitMQ\n"
        "- Ставь навык Brag одной командой\n",
        tools=tools,
        raw_text=raw,
    )
    assert "RabbitMQ" not in dig
    assert "Redis" not in dig
    assert "Brag" in dig


def test_non_product_tools_dropped():
    """Screen fragments / README examples are not tools (generic shape gates)."""
    from app.services.resources import drop_non_product_tools

    raw = (
        "[0:00] Открываешь Claude и Cursor, ставишь Logo Design Skill.\n"
        "[On-screen OCR timed]\n"
        "[OCR 0:10] README MIT license Kiln Specialty Zestly Food Pro Max 49\n"
    )
    out = drop_non_product_tools(
        ["Claude", "Cursor", "Logo Design Skill", "README MIT", "Kiln Specialty",
         "Zestly Food", "Pro Max 49", "D DTF VC", "Q Wordpress", "Sive 80"],
        raw,
    )
    assert "Claude" in out
    assert "Logo Design Skill" in out
    for junk in ("README MIT", "Kiln Specialty", "Zestly Food", "Pro Max 49",
                 "D DTF VC", "Q Wordpress", "Sive 80"):
        assert junk not in out


def test_missing_link_score_cap_for_scanner_tip():
    card = KnowledgeCard(
        title="Бесплатный сканер NVIDIA",
        summary="NVIDIA выложила открытый сканер скриптов. Скачайте и гоняйте до установки.",
        main_idea="Сканируй skills до install.",
        digest="• Скачайте сканер с GitHub\n• Запускайте до установки\n• Отклоняйте критический риск",
        key_ideas=["Не ставь skills вслепую"],
        practical_takeaways=["Гоняй SkillSpector до install"],
        tools=["NVIDIA", "SkillSpector"],
        technologies=[],
        links=[],
        category="AI",
        tags=["security"],
        usefulness_signals=UsefulnessSignals(
            actionability=5, novelty=4, specificity=5, reuse_likelihood=5, personal_fit=5
        ),
        usefulness_score=10,
        usefulness_reason="fit",
    )
    assert card.usefulness_score <= 7

