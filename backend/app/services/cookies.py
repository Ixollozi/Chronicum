"""Netscape cookies.txt management for Instagram / TikTok / YouTube downloads."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.storage import ensure_storage

MANAGED_COOKIES_FILENAME = "media_cookies.txt"
MAX_COOKIES_BYTES = 2 * 1024 * 1024  # 2 MiB cap for pasted/uploaded cookies.txt

_TRACKED_PLATFORMS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com", "cdninstagram.com"),
    "tiktok": ("tiktok.com",),
    "youtube": ("youtube.com", "google.com"),
}


@dataclass
class CookiesInspection:
    configured: bool
    source: str  # "env" | "managed" | "browser" | "none"
    path: str | None
    browser: str | None
    entries: int
    domains: list[str]
    platforms: dict[str, bool]
    writable: bool


def managed_cookies_path() -> Path:
    return ensure_storage() / MANAGED_COOKIES_FILENAME


def resolve_cookies_file(settings: Any | None = None) -> str:
    """
    Return the active Netscape cookies.txt path:
      1. Explicit `MEDIA_COOKIES_FILE` from environment if it exists.
      2. Managed `<STORAGE_PATH>/media_cookies.txt` uploaded via Web Settings.
    """
    cfg = settings or get_settings()
    env_path = (getattr(cfg, "media_cookies_file", "") or "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    managed = managed_cookies_path()
    if managed.is_file():
        return str(managed)
    return env_path


def validate_netscape_cookies(content: str) -> tuple[str, int, list[str]]:
    """
    Validate a Netscape HTTP Cookie File payload and return
    `(normalized_content, valid_cookie_entries, sorted_unique_domains)`.
    Raises ValueError if content is empty, oversized, or has no valid 7-column cookie rows.
    """
    if not content or not content.strip():
        raise ValueError("Пустой файл cookies. Экспортируй Netscape cookies.txt из браузера.")
    raw_bytes = content.encode("utf-8", errors="ignore")
    if len(raw_bytes) > MAX_COOKIES_BYTES:
        raise ValueError("Файл cookies слишком большой (максимум 2 МБ).")

    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    valid_entries = 0
    domains: set[str] = set()
    has_header = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "Netscape HTTP Cookie File" in line or "HTTP Cookie File" in line:
                has_header = True
            # #HttpOnly_.domain.com lines are valid cookie rows in Netscape format
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_") :]
            else:
                continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain = parts[0].strip().lstrip(".").lower()
        flag = parts[1].strip().upper()
        secure = parts[3].strip().upper()
        if not domain or "." not in domain:
            continue
        if flag not in {"TRUE", "FALSE"} or secure not in {"TRUE", "FALSE"}:
            continue
        valid_entries += 1
        domains.add(domain)

    if valid_entries == 0:
        raise ValueError(
            "Не найдено валидных строк Netscape cookies.txt (7 колонок через Tab). "
            "Используй расширение «Get cookies.txt LOCALLY» в формате Netscape."
        )

    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    if not has_header:
        normalized = "# Netscape HTTP Cookie File\n" + normalized
    return normalized, valid_entries, sorted(domains)


def _platform_coverage(domains: list[str]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for platform, suffixes in _TRACKED_PLATFORMS.items():
        out[platform] = any(
            d == sfx or d.endswith(f".{sfx}") for d in domains for sfx in suffixes
        )
    return out


def inspect_cookies_file(settings: Any | None = None) -> CookiesInspection:
    cfg = settings or get_settings()
    env_path = (getattr(cfg, "media_cookies_file", "") or "").strip()
    browser = (getattr(cfg, "media_cookies_from_browser", "") or "").strip() or None
    managed = managed_cookies_path()

    active_path: Path | None = None
    source = "none"
    if env_path and Path(env_path).is_file():
        active_path = Path(env_path)
        source = "env"
    elif managed.is_file():
        active_path = managed
        source = "managed"
    elif browser:
        source = "browser"

    entries = 0
    domains: list[str] = []
    if active_path is not None:
        try:
            text = active_path.read_text(encoding="utf-8", errors="ignore")
            _, entries, domains = validate_netscape_cookies(text)
        except Exception:
            entries = 0
            domains = []

    return CookiesInspection(
        configured=bool(entries > 0 or browser),
        source=source,
        path=str(active_path) if active_path is not None else None,
        browser=browser,
        entries=entries,
        domains=domains[:30],
        platforms=_platform_coverage(domains),
        writable=(source != "env"),
    )


def save_cookies_content(content: str, *, settings: Any | None = None) -> CookiesInspection:
    normalized, _, _ = validate_netscape_cookies(content)
    target = managed_cookies_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".txt.tmp")
    tmp.write_text(normalized, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)
    return inspect_cookies_file(settings)


def delete_cookies_file(*, settings: Any | None = None) -> CookiesInspection:
    target = managed_cookies_path()
    if target.is_file():
        target.unlink(missing_ok=True)
    return inspect_cookies_file(settings)
