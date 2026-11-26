"""Groq LLM model readiness probe (startup + /health)."""

from __future__ import annotations

from app.config import Settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_groq_llm_ok: bool | None = None
_groq_llm_error: str | None = None


def get_groq_llm_status() -> tuple[bool | None, str | None]:
    return _groq_llm_ok, _groq_llm_error


async def probe_groq_llm(settings: Settings) -> bool:
    """Check that configured GROQ_LLM_MODEL is listed for this API key."""
    global _groq_llm_ok, _groq_llm_error
    key = (settings.groq_api_key or "").strip()
    model = (settings.groq_llm_model or "").strip()
    if not key or key.lower() in {"replace_me", "changeme", "xxx"}:
        _groq_llm_ok = False
        _groq_llm_error = "GROQ_API_KEY missing"
        return False
    if not model:
        _groq_llm_ok = False
        _groq_llm_error = "GROQ_LLM_MODEL empty"
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if r.status_code != 200:
                _groq_llm_ok = False
                _groq_llm_error = f"models HTTP {r.status_code}"
                log.warning("groq_llm_probe_failed", status=r.status_code, body=r.text[:200])
                return False
            ids = {m.get("id") for m in (r.json().get("data") or []) if isinstance(m, dict)}
            if model not in ids:
                _groq_llm_ok = False
                _groq_llm_error = f"model not available: {model}"
                log.warning("groq_llm_model_missing", model=model, available=sorted(ids)[:20])
                return False
            _groq_llm_ok = True
            _groq_llm_error = None
            log.info("groq_llm_ok", model=model)
            return True
    except Exception as exc:
        _groq_llm_ok = False
        _groq_llm_error = str(exc)[:200]
        log.warning("groq_llm_probe_error", error=_groq_llm_error)
        return False
