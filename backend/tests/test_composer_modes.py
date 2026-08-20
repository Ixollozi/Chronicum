from app.services.composer import (
    classify_intent,
    normalize_mode,
    strip_mode_prefix,
)


def test_normalize_mode():
    assert normalize_mode("auto") == "auto"
    assert normalize_mode("спросить") == "ask"
    assert normalize_mode("задача") == "task"
    assert normalize_mode("правило") == "rule"
    assert normalize_mode("weird") == "auto"


def test_slash_prefix():
    text, mode = strip_mode_prefix("/задача купить домен")
    assert mode == "task"
    assert text == "купить домен"
    text2, mode2 = strip_mode_prefix("просто мысль")
    assert mode2 is None
    assert text2 == "просто мысль"


def test_classify_intent_basics():
    assert classify_intent("https://youtu.be/abc12345678") == "url"
    assert classify_intent("Как сделать оффер?") == "search"
    assert classify_intent("странная мысль про позиционирование") == "note"
