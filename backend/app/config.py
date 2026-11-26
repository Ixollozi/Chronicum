from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDERS = {"", "replace_me", "changeme", "xxx"}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_CANDIDATES = (
    PROJECT_ROOT / ".env",
    Path.cwd() / ".env",
    Path(__file__).resolve().parents[1] / ".env",
)


def _env_files() -> tuple[str, ...]:
    return tuple(str(p) for p in _ENV_CANDIDATES if p.exists()) or (str(PROJECT_ROOT / ".env"),)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(default="replace_me")
    telegram_owner_id: int = 0
    groq_api_key: str = Field(default="replace_me")
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'data' / 'chronicum.db').as_posix()}"
    )
    redis_url: str = Field(default="redis://127.0.0.1:6379/0")

    chronicum_lite: bool = True

    ai_provider: str = "groq"
    transcribe_provider: str = "groq"
    groq_llm_model: str = "llama-3.3-70b-versatile"
    groq_whisper_model: str = "whisper-large-v3"
    # Whisper language: "auto" = omit (detect), or ISO code like "ru" / "en"
    whisper_language: str = "auto"
    whisper_max_file_mb: float = 20.0
    whisper_chunk_seconds: int = 600
    groq_vision_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    google_ai_api_key: str = ""
    google_ai_model: str = "gemini-2.0-flash"

    video_preview_threshold_min: int = 10
    video_full_score_threshold: int = 6
    video_preview_fraction: float = 0.2
    video_concurrency: int = 1
    arq_max_jobs: int = 2
    max_api_retries: int = 3

    storage_path: str = str(PROJECT_ROOT / "data" / "files")
    # Optional: Netscape cookies.txt for Instagram when Meta rate-limits anonymous downloads
    media_cookies_file: str = ""
    # Optional: chrome | firefox | edge — yt-dlp --cookies-from-browser
    media_cookies_from_browser: str = ""
    log_level: str = "INFO"
    allow_placeholder_secrets: bool = True
    web_api_token: str = "chronicum-dev-token"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @field_validator("ai_provider", "transcribe_provider")
    @classmethod
    def normalize_provider(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def is_lite(self) -> bool:
        return bool(self.chronicum_lite) or self.database_url.startswith("sqlite")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_ready(self) -> bool:
        return self.telegram_bot_token.strip() not in _PLACEHOLDERS

    @property
    def secrets_ready(self) -> bool:
        if self.allow_placeholder_secrets:
            return self.telegram_ready and self.ai_ready
        return bool(self.telegram_bot_token and self.groq_api_key)

    @property
    def ai_ready(self) -> bool:
        return self.groq_api_key.strip() not in _PLACEHOLDERS or (
            self.ai_provider == "google" and bool(self.google_ai_api_key.strip())
        )

    def require_telegram(self) -> None:
        if not self.telegram_ready:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN still a placeholder. "
                "Put the real token from @BotFather into .env and restart the bot."
            )

    def require_secrets(self) -> None:
        missing = []
        if self.telegram_bot_token.strip() in _PLACEHOLDERS:
            missing.append("TELEGRAM_BOT_TOKEN")
        if self.groq_api_key.strip() in _PLACEHOLDERS and self.ai_provider == "groq":
            missing.append("GROQ_API_KEY")
        if missing:
            raise RuntimeError(
                f"Missing real secrets (still placeholders): {', '.join(missing)}. "
                "Copy .env.example → .env and fill them."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
