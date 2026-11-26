from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth_routes import router as auth_router
from app.api.routes import router as api_router
from app.config import get_settings
from app.logging_setup import get_logger, setup_logging
from app.mcp_http import build_mcp_asgi, mcp_http_enabled
from app.middleware_access import AccessLogMiddleware

settings = get_settings()
setup_logging(settings.log_level)
log = get_logger(__name__)
_mcp_server, _mcp_asgi = build_mcp_asgi()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    current = get_settings()
    current.validate_security_posture()
    for warning in current.security_warnings():
        log.warning("security_posture_warning", detail=warning)
    if current.is_lite:
        from app.db import init_lite_db
        from app.services.queue import recover_lite_queue, start_lite_watchdog

        await init_lite_db()
        try:
            await recover_lite_queue()
        except Exception as e:  # noqa: BLE001 — startup must not die on reclaim
            log.warning("startup_reclaim_failed", error=str(e)[:300])
        start_lite_watchdog()
    else:
        try:
            from app.services.queue import reclaim_and_enqueue

            await reclaim_and_enqueue()
        except Exception as e:  # noqa: BLE001 — startup must not die on reclaim
            log.warning("startup_reclaim_failed", error=str(e)[:300])
    if current.ai_provider == "groq" and current.ai_ready:
        from app.services.groq_health import probe_groq_llm

        await probe_groq_llm(current)
    manager = getattr(_mcp_server, "session_manager", None) if _mcp_server is not None else None
    try:
        if manager is not None:
            async with manager.run():
                yield
        else:
            yield
    finally:
        if current.is_lite:
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
app.add_middleware(AccessLogMiddleware)
app.include_router(auth_router)
app.include_router(api_router)
if _mcp_asgi is not None:
    app.mount("/mcp", _mcp_asgi)


@app.get("/health")
async def health() -> JSONResponse:
    s = get_settings()
    from app.services.groq_health import get_groq_llm_status

    groq_ok, groq_err = get_groq_llm_status()
    usage_alert = None
    try:
        from datetime import datetime, timezone

        from sqlalchemy import func, select

        from app.db import SessionLocal
        from app.models import Material

        # Soft daily budget (tokens in+out). Not a hard stop — warning for free-tier.
        soft_limit = 250_000
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(Material.usage_tokens_in), 0),
                        func.coalesce(func.sum(Material.usage_tokens_out), 0),
                    ).where(Material.updated_at >= day_start)
                )
            ).one()
            used = int(row[0] or 0) + int(row[1] or 0)
            if used >= soft_limit:
                usage_alert = {
                    "level": "warn",
                    "message": f"Groq usage today ~{used} tokens (soft limit {soft_limit})",
                    "tokens_today": used,
                    "soft_limit": soft_limit,
                }
            else:
                usage_alert = {
                    "level": "ok",
                    "tokens_today": used,
                    "soft_limit": soft_limit,
                }
    except Exception:
        usage_alert = None

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
            "groq_llm_ok": groq_ok,
            "groq_llm_error": groq_err,
            "usage_alert": usage_alert,
            "web_ready": True,
            "mcp_http": mcp_http_enabled(),
            "mcp_path": "/mcp" if mcp_http_enabled() else None,
        }
    )
