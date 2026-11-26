"""Tests for link harvest/normalize and code_blocks on KnowledgeCard."""

from __future__ import annotations


def test_coerce_and_merge_links():
    from app.services.resources import coerce_link_items, harvest_resources, merge_card_resources

    coerced = coerce_link_items(
        [
            "https://pexels.com/foo",
            {"url": "https://www.pexels.com/foo/", "title": "Pexels", "source": "llm"},
            "not a url",
        ]
    )
    assert len(coerced) == 1
    assert coerced[0]["title"] == "Pexels"
    assert coerced[0]["source"] == "llm"

    text = """
[0:10] Go to github.com/openai/whisper and also https://docs.python.org/3/
[On-screen OCR timed]
[OCR 0:12] www.npmjs.com/package/react
"""
    h = harvest_resources(text)
    urls = " ".join(x["url"] for x in h.links)
    assert "github.com/openai/whisper" in urls
    assert "docs.python.org" in urls
    assert "npmjs.com" in urls
    assert any(x.get("source") == "speech" for x in h.links)
    assert any(x.get("source") == "ocr" for x in h.links)

    tools, links = merge_card_resources(
        ["CapCut"],
        [{"url": "https://docs.python.org/3/", "title": "Python docs", "source": "llm"}],
        h,
        raw_text=text,
    )
    # CapCut not in speech/OCR → ungrounded LLM tool dropped
    assert "CapCut" not in tools
    py = next(x for x in links if "docs.python.org" in x["url"])
    assert py["title"] == "Python docs"


def test_knowledge_card_code_and_links():
    from app.ai.schemas import KnowledgeCard, UsefulnessSignals

    card = KnowledgeCard.model_validate(
        {
            "title": "Snippet demo",
            "summary": "A short summary about extracting light code from the material.",
            "main_idea": "Light code belongs in a dedicated code tab.",
            "digest": "• Save snippets\n• Keep URLs separately",
            "key_ideas": ["Separate code from prose", "Capture real URLs"],
            "practical_takeaways": ["Copy the CSS block into your project"],
            "tools": ["VS Code"],
            "technologies": ["CSS"],
            "links": ["https://developer.mozilla.org/en-US/docs/Web/CSS", {"url": "mdn.io/flex", "title": "MDN Flex"}],
            "code_blocks": [
                {
                    "title": "Flex center",
                    "language": "css",
                    "code": ".box { display: flex; place-items: center; }",
                }
            ],
            "category": "",
            "tags": ["css"],
            "usefulness_signals": {
                "actionability": 4,
                "novelty": 3,
                "specificity": 4,
                "reuse_likelihood": 4,
                "personal_fit": 3,
            },
            "usefulness_reason": "Concrete CSS snippet with a clear reuse path.",
        }
    )
    assert len(card.code_blocks) == 1
    assert card.code_blocks[0].language == "css"
    assert all(hasattr(x, "url") for x in card.links)
    assert any("mozilla.org" in x.url for x in card.links)
