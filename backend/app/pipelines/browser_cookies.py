from __future__ import annotations

from pathlib import Path


def netscape_cookies_for_host(path: str, host: str) -> dict[str, str]:
    file = Path(path)
    if not path or not file.exists():
        return {}
    needle = (host or "").lower().lstrip(".")
    jar: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain = parts[0].lower().lstrip(".")
        if needle and needle not in domain:
            continue
        jar[parts[5]] = parts[6]
    return jar


def cookie_header(path: str, host: str) -> str:
    jar = netscape_cookies_for_host(path, host)
    return "; ".join(f"{k}={v}" for k, v in jar.items())
