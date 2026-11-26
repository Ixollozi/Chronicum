"""Golden reel fixtures: offline gates for mcp_safe readiness."""

from __future__ import annotations

from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "golden_reels"


def _raw(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "name,must_tools,must_not,must_link,title",
    [
        (
            "product_brag.txt",
            ["brag"],
            ["fork you", "create pr", "code iss"],
            "latent-spaces/brag",
            "Навык Brag: промо-ролик",
        ),
        (
            "listicle_system.txt",
            ["websocket", "circuit breaker"],
            [],
            None,
            "Шпаргалка System Design",
        ),
        (
            "offtopic_music.txt",
            ["gemini"],
            ["claude"],
            None,
            "Music tip",
        ),
        (
            "cta_heavy.txt",
            ["seo monster"],
            ["your assistant", "run seo"],
            "avansaber/seo-monster",
            "SEO Monster MCP",
        ),
    ],
)
def test_golden_harvest_and_merge(name, must_tools, must_not, must_link, title):
    from app.services.resources import (
        ground_digest_lines,
        harvest_resources,
        merge_card_resources,
    )
    from app.services.trust import trust_score

    raw = _raw(name)
    h = harvest_resources(raw, duration_seconds=45, title=title)
    tools, links = merge_card_resources(
        list(h.tools),
        list(h.links),
        h,
        raw_text=raw,
    )
    blob = " ".join(tools).lower()
    for need in must_tools:
        assert need in blob, f"{name}: missing tool {need} in {tools}"
    for bad in must_not:
        assert bad not in blob, f"{name}: junk tool {bad} in {tools}"
    urls = " ".join(str(x.get("url") or "").lower() for x in links)
    if must_link:
        assert must_link in urls, f"{name}: missing link {must_link} in {urls}"

    dig = ground_digest_lines(
        "- Напиши ПРОМПТ в комментариях за журнал\n"
        "- Сделай полезный шаг из видео\n",
        tools=tools,
        raw_text=raw,
    )
    # CTA alone should not dominate when other content can exist
    if "cta_heavy" in name or "product_brag" in name:
        assert "промпт" not in dig.lower() or dig.count("-") <= 1

    tr = trust_score(
        raw_text=raw,
        title=title,
        tools=tools,
        links=links,
        digest=dig
        or "\n".join(f"- Используй {t}" for t in tools[:2])
        or "- Мало данных",
        evidence=[
            {"kind": "speech", "text": "x" * 80, "confidence": 0.8},
            {"kind": "ocr", "text": "y" * 40, "confidence": 0.7},
        ],
    )
    assert tr.level in {"mcp_safe", "review", "weak"}
    assert 0.0 <= tr.score <= 1.0
