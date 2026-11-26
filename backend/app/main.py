from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router as api_router
from app.config import get_settings
from app.logging_setup import setup_logging
from app.mcp_http import build_mcp_asgi, mcp_http_enabled

settings = get_settings()
setup_logging(settings.log_level)
_mcp_server, _mcp_asgi = build_mcp_asgi()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.is_lite:
        from app.db import init_lite_db
        from app.services.queue import recover_lite_queue

        await init_lite_db()
        await recover_lite_queue()
    manager = getattr(_mcp_server, "session_manager", None) if _mcp_server is not None else None
    try:
        if manager is not None:
            async with manager.run():
                yield
        else:
            yield
    finally:
        if settings.is_lite:
            from app.services.queue import shutdown_lite_queue

            await shutdown_lite_queue()


app = FastAPI(title="Chronicum", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
if _mcp_asgi is not None:
    app.mount("/mcp", _mcp_asgi)


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
            "mcp_http": mcp_http_enabled(),
            "mcp_path": "/mcp" if mcp_http_enabled() else None,
        }
    )
