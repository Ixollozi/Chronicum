from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router as api_router
from app.config import get_settings
from app.logging_setup import setup_logging

settings = get_settings()
setup_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.is_lite:
        from app.db import init_lite_db

        await init_lite_db()
    yield


app = FastAPI(title="Chronicum", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/health")
async def health() -> JSONResponse:
    s = get_settings()
    return JSONResponse(
        {
            "status": "ok",
            "mode": "lite" if s.is_lite else "full",
            "secrets_ready": s.secrets_ready,
            "telegram_ready": s.telegram_ready,
            "ai_ready": s.ai_ready,
            "ai_provider": s.ai_provider,
            "transcribe_provider": s.transcribe_provider,
            "groq_llm_model": s.groq_llm_model,
            "web_ready": True,
        }
    )
