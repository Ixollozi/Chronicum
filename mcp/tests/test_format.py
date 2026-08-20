from __future__ import annotations

from chronicum_mcp.format import list_card, project_material, search_payload, taxonomy_payload


def test_list_card_clips_summary():
    card = list_card(
        {
            "id": "abc",
            "title": "Note",
            "usefulness_score": 8,
            "processing_status": "done",
            "category": "AI",
            "tags": ["llm"],
            "source_type": "note",
            "summary": "x" * 400,
        }
    )
    assert card["id"] == "abc"
    assert card["score"] == 8
    assert card["summary"].endswith("…")
    assert len(card["summary"]) <= 280


def test_search_payload():
    out = search_payload(
        {
            "answer": "Use FastMCP.",
            "sources": [{"id": "1", "title": "MCP", "usefulness_score": 7, "processing_status": "done", "summary": "hi"}],
        }
    )
    assert out["answer"] == "Use FastMCP."
    assert out["sources"][0]["id"] == "1"


def test_project_material_levels():
    detail = {
        "id": "m1",
        "title": "Card",
        "source_type": "note",
        "source_url": None,
        "summary": "short",
        "main_idea": "idea",
        "category": "AI",
        "tags": ["mcp"],
        "usefulness_score": 9,
        "processing_status": "done",
        "processing_stage": "full",
        "digest": "• a",
        "practical_takeaways": ["do x"],
        "raw_text": "SECRET RAW",
        "usage_tokens_in": 12,
    }
    summary = project_material(detail, "summary")
    assert "digest" not in summary
    assert "raw_text" not in summary

    card = project_material(detail, "card")
    assert card["digest"] == "• a"
    assert "raw_text" not in card

    full = project_material(detail, "full")
    assert full["raw_text"] == "SECRET RAW"
    assert "usage_tokens_in" not in full


def test_project_material_rejects_bad_level():
    try:
        project_material({"id": "x"}, "nope")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_taxonomy_truncates_tags():
    tags = [{"id": i, "name": f"t{i}", "status": "approved"} for i in range(250)]
    payload = taxonomy_payload([{"id": 1, "name": "AI"}], tags)
    assert len(payload["tags"]) == 200
    assert payload["tags_truncated"] is True
