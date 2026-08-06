"""Lightweight preview API with sample data — no Postgres/Redis required.

Run:  .\\.venv\\Scripts\\python.exe scripts\\preview_api.py
"""

from __future__ import annotations

from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

TOKEN = "chronicum-dev-token"
NOW = datetime.now(timezone.utc).isoformat()

MATERIALS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Как строить базу знаний без хаоса тегов",
        "source_type": "article",
        "source_url": "https://example.com/knowledge",
        "summary": "Короткий разбор управляемой таксономии: фиксированные категории, словарь тегов и pending-подтверждение.",
        "main_idea": "Свободные AI-теги со временем разъезжаются — словарь нужно вести.",
        "category": "AI",
        "tags": ["knowledge-base", "taxonomy"],
        "usefulness_score": 8,
        "processing_status": "done",
        "processing_stage": "full",
        "created_at": NOW,
        "raw_text": "Demo transcript / article body for Chronicum preview.",
        "key_ideas": [
            "Категории сверху, теги снизу",
            "Новые теги — в pending",
            "Оценка полезности не скрывает материал",
        ],
        "practical_takeaways": ["Завести seed-категории до первого импорта"],
        "tools": ["Telegram", "Postgres"],
        "technologies": ["FastAPI", "Next.js"],
        "links": [],
        "usefulness_reason": "Прямо про архитектуру Chronicum",
        "error_message": None,
        "duration_seconds": None,
        "usage_tokens_in": 1200,
        "usage_tokens_out": 400,
        "usage_audio_seconds": 0,
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "title": "Two-stage обработка длинного видео",
        "source_type": "youtube",
        "source_url": "https://youtube.com/watch?v=demo",
        "summary": "Preview по первым 20% экономит Whisper/LLM на «воде»; full — по порогу или кнопке.",
        "main_idea": "Дорогой пайплайн нельзя гонять вслепую.",
        "category": "Программирование",
        "tags": ["video", "cost-control?"],
        "usefulness_score": 7,
        "processing_status": "awaiting_full",
        "processing_stage": "preview",
        "created_at": NOW,
        "raw_text": "Preview transcript only…",
        "key_ideas": ["Threshold по минутам", "Lazy /process_full"],
        "practical_takeaways": ["На первой неделе логировать bin score 4–6"],
        "tools": ["ffmpeg", "Groq Whisper"],
        "technologies": ["ARQ"],
        "links": [],
        "usefulness_reason": "Полезно, но полный контекст ещё не извлечён",
        "error_message": None,
        "duration_seconds": 1860,
        "usage_tokens_in": 800,
        "usage_tokens_out": 220,
        "usage_audio_seconds": 372,
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "title": "Заметка: идеи для вебинара по агентствам",
        "source_type": "note",
        "source_url": None,
        "summary": "Черновые тезисы: кейсы, оффер, структура воронки.",
        "main_idea": "Вебинар должен продавать следующий шаг, а не «знания вообще».",
        "category": "Маркетинг",
        "tags": ["webinar"],
        "usefulness_score": 6,
        "processing_status": "done",
        "processing_stage": "full",
        "created_at": NOW,
        "raw_text": "Структура: хук → проблема → кейс → оффер.",
        "key_ideas": ["Один CTA", "Социальное доказательство в середине"],
        "practical_takeaways": ["Снять 3 коротких кейса до среды"],
        "tools": [],
        "technologies": [],
        "links": [],
        "usefulness_reason": "Рабочая заметка, средняя плотность",
        "error_message": None,
        "duration_seconds": None,
        "usage_tokens_in": 300,
        "usage_tokens_out": 180,
        "usage_audio_seconds": 0,
    },
]

app = FastAPI(title="Chronicum Preview API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _auth(authorization: str | None) -> None:
    if not authorization:
        return
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Invalid auth")
    token = authorization.split(" ", 1)[1].strip()
    if token != TOKEN:
        raise HTTPException(401, "Invalid token")


def _list_item(m: dict) -> dict:
    keys = [
        "id",
        "title",
        "source_type",
        "source_url",
        "summary",
        "main_idea",
        "category",
        "tags",
        "usefulness_score",
        "processing_status",
        "processing_stage",
        "created_at",
    ]
    return {k: m[k] for k in keys}


@app.get("/health")
def health():
    return {"status": "ok", "mode": "preview", "web_ready": True}


@app.get("/api/materials")
def list_materials(authorization: str | None = Header(default=None), q: str | None = None, status: str | None = None):
    _auth(authorization)
    items = MATERIALS
    if status:
        items = [m for m in items if m["processing_status"] == status]
    if q:
        ql = q.lower()
        items = [
            m
            for m in items
            if ql in (m["title"] or "").lower()
            or ql in (m["summary"] or "").lower()
            or ql in (m["main_idea"] or "").lower()
        ]
    return {"items": [_list_item(m) for m in items], "total": len(items)}


@app.get("/api/materials/{material_id}")
def get_material(material_id: str, authorization: str | None = Header(default=None)):
    _auth(authorization)
    for m in MATERIALS:
        if m["id"] == material_id:
            return m
    raise HTTPException(404, "Not found")


@app.patch("/api/materials/{material_id}/score")
def score(material_id: str, body: dict, authorization: str | None = Header(default=None)):
    _auth(authorization)
    for m in MATERIALS:
        if m["id"] == material_id:
            m["usefulness_score"] = int(body.get("score", m["usefulness_score"] or 5))
            m["usefulness_reason"] = (m.get("usefulness_reason") or "") + " [preview]"
            return m
    raise HTTPException(404, "Not found")


@app.post("/api/materials/{material_id}/retry")
@app.post("/api/materials/{material_id}/process-full")
def noop_job(material_id: str, authorization: str | None = Header(default=None)):
    _auth(authorization)
    return {"id": material_id, "status": "queued", "message": "Preview mode — job simulated"}


@app.post("/api/materials/note")
@app.post("/api/materials/url")
def create_simple(authorization: str | None = Header(default=None)):
    _auth(authorization)
    return {
        "id": MATERIALS[0]["id"],
        "status": "done",
        "message": "Preview mode — материал не создаётся, открой демо-карточку",
    }


@app.post("/api/materials/upload")
async def upload(authorization: str | None = Header(default=None)):
    _auth(authorization)
    return {
        "id": MATERIALS[0]["id"],
        "status": "done",
        "message": "Preview mode — upload simulated",
    }


@app.post("/api/search")
def search(body: dict, authorization: str | None = Header(default=None)):
    _auth(authorization)
    q = (body.get("query") or "").strip()
    return {
        "answer": (
            f"Это preview-режим без Postgres/Groq.\n\n"
            f"На вопрос «{q}» обычно Chronicum собрал бы ответ по FTS + LLM "
            f"и приложил источники ниже."
        ),
        "sources": [_list_item(m) for m in MATERIALS[:2]],
    }


@app.get("/api/categories")
def categories(authorization: str | None = Header(default=None)):
    _auth(authorization)
    return [{"id": 1, "name": "AI"}, {"id": 2, "name": "Маркетинг"}, {"id": 3, "name": "Программирование"}]


@app.get("/api/tags")
def tags(authorization: str | None = Header(default=None)):
    _auth(authorization)
    return [{"id": 1, "name": "knowledge-base", "status": "approved"}]


if __name__ == "__main__":
    print("Preview API on http://127.0.0.1:8000  (token: chronicum-dev-token)")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
