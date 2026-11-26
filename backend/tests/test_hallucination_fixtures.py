"""Wave 0 fixtures: class-based regression baseline (offline, no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "classes"


def _raw(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_fixture_classes_harvest_baseline():
    from app.services.resources import harvest_resources

    cases = {
        "listicle_github.txt": ("rybbit", "github.com"),
        # Repo URL is NOT invented in harvest anymore — resolved online at apply time
        "single_tool_tip.txt": ("skillspector", None),
        "tutorial_code.txt": (None, None),
        "cta_promo.txt": (None, None),
        "non_ru_en.txt": ("rybbit", "github.com"),
    }
    for name, (tool_hit, link_hit) in cases.items():
        h = harvest_resources(_raw(name), duration_seconds=40)
        blob = " ".join(h.tools).lower()
        urls = " ".join(x["url"].lower() for x in h.links)
        if tool_hit:
            assert tool_hit in blob, name
        if link_hit:
            assert link_hit in urls, name


def test_empty_speech_is_weak():
    from app.services.resources import is_weak_speech

    assert is_weak_speech(_raw("empty_speech.txt"))


def test_talking_head_no_tools():
    from app.services.resources import harvest_resources

    h = harvest_resources(_raw("talking_head.txt"), duration_seconds=60)
    assert h.tools == []


@pytest.mark.asyncio
async def test_clients_fixtures_parse(tmp_path):
    from app.services.clients import parse_paste

    wa = parse_paste((Path(__file__).parent / "fixtures" / "clients" / "paste_whatsapp.txt").read_text(encoding="utf-8"))
    assert len(wa) >= 3
    assert {m["speaker_name"] for m in wa} >= {"Иван Петров", "Самир"}
    assert wa[0]["event_at"] is not None

    tg = parse_paste((Path(__file__).parent / "fixtures" / "clients" / "paste_telegram.txt").read_text(encoding="utf-8"))
    assert len(tg) >= 2
