from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai import GoogleLLMClient
from app.services.enrichment import _ground_explanation


def test_ground_explanation_keeps_sections_grounded_in_links_summary_and_raw_text():
    material = SimpleNamespace(
        title="Создание 3D-игры на Godot и Blender с помощью ИИ",
        source_url="https://www.youtube.com/watch?v=Pirm1NA0yj4",
        summary=(
            "Автор Game Studio разбирает цикл разработки игры Wild Waste в Blender и Godot "
            "с экспортом HTML-билда на itch.io."
        ),
        main_idea="Модульные сцены Godot лучше сплошного сгенерированного кода.",
        digest="- Создайте 3D-модели в Blender.\n- Соберите уровень в Godot и опубликуйте на itch.io.",
        key_ideas=["Модульная структура сцен упрощает правки."],
        practical_takeaways=["Экспортируйте проект Godot в HTML и выложите на itch.io."],
        tools=["Godot", "Blender", "ChatGPT", "Cursor"],
        links=[
            {"url": "https://studioigor.itch.io/wild-waste", "title": "Игра Wild Waste на itch.io"},
            {"url": "https://t.me/studioigor", "title": "Telegram-канал автора"},
            {"url": "https://boosty.to/studioigor", "title": "Boosty автора"},
        ],
        raw_text="[0:10] Привет, это канал Game Studio. Сегодня делаем игру на Godot и Blender и выкладываем HTML билд.",
    )

    explanation = """## Суть

Ролик Game Studio показывает создание игры Wild Waste с помощью Blender, Godot и ChatGPT.

## Шаги

1. Подготовьте концепт и 3D-модели в Blender через Cursor.
2. Соберите модульные сцены в Godot и сделайте HTML-экспорт.
3. Разверните бэкенд через Kubernetes и Terraform в AWS.

## Как применить

Используйте модульный подход Godot для быстрого прототипирования и публикации на itch.io.

## Источники

- [Игра Wild Waste на itch.io](https://studioigor.itch.io/wild-waste)
- [Telegram-канал автора](https://t.me/studioigor)
- [Boosty автора](https://boosty.to/studioigor)
"""

    grounded = _ground_explanation(explanation, material)

    assert "## Суть" in grounded
    assert "Wild Waste" in grounded
    assert "## Шаги" in grounded
    assert "1. Подготовьте концепт и 3D-модели в Blender через Cursor." in grounded
    assert "2. Соберите модульные сцены в Godot и сделайте HTML-экспорт." in grounded
    # Hallucinated step mentioning ungrounded infra brands is dropped without wiping valid sibling steps
    assert "Kubernetes" not in grounded
    assert "Terraform" not in grounded
    assert "## Как применить" in grounded
    assert "itch.io" in grounded
    assert "## Источники" in grounded
    assert "https://studioigor.itch.io/wild-waste" in grounded
    assert "https://t.me/studioigor" in grounded
    assert "https://boosty.to/studioigor" in grounded


@pytest.mark.asyncio
async def test_google_llm_client_extracts_usage_metadata_tokens(monkeypatch):
    card_payload = json.dumps(
        {
            "title": "Test card",
            "summary": "Короткое резюме.",
            "main_idea": "Главная мысль.",
            "digest": "- Пункт 1",
            "key_ideas": ["Идея 1"],
            "practical_takeaways": ["Шаг 1"],
            "tools": ["Godot"],
            "links": [],
            "category": "AI",
            "tags": ["AI"],
            "usefulness_score": 7,
            "usefulness_reason": "Полезно",
            "usefulness_signals": {
                "specificity": 4,
                "novelty": 3,
                "actionability": 4,
                "reuse_likelihood": 4,
                "personal_fit": 4,
            },
        }
    )

    settings = SimpleNamespace(google_ai_model="gemini-flash-lite-latest")
    client = GoogleLLMClient(settings, api_key="test-key")

    fake_resp = SimpleNamespace(
        text=card_payload,
        usage_metadata=SimpleNamespace(prompt_token_count=8420, candidates_token_count=615, total_token_count=9035),
    )
    monkeypatch.setattr(client, "_generate_sync", lambda _p, _cfg: fake_resp)
    res = await client.complete_json("sys", "usr")
    assert res.provider == "google"
    assert res.tokens_in == 8420
    assert res.tokens_out == 615

    # Fallback to total_token_count - prompt_token_count when candidates_token_count is missing
    fake_resp_total = SimpleNamespace(
        text=card_payload,
        usage_metadata={"prompt_token_count": 3000, "total_token_count": 3450},
    )
    monkeypatch.setattr(client, "_generate_sync", lambda _p, _cfg: fake_resp_total)
    res2 = await client.complete_json("sys", "usr")
    assert res2.tokens_in == 3000
    assert res2.tokens_out == 450

    # Missing usage_metadata safely yields 0, 0
    fake_resp_none = SimpleNamespace(text=card_payload, usage_metadata=None)
    monkeypatch.setattr(client, "_generate_sync", lambda _p, _cfg: fake_resp_none)
    res3 = await client.complete_json("sys", "usr")
    assert res3.tokens_in == 0
    assert res3.tokens_out == 0
