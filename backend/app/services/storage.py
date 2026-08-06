import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from app.config import get_settings


def ensure_storage() -> Path:
    root = Path(get_settings().storage_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_bytes(data: bytes, suffix: str) -> str:
    root = ensure_storage()
    name = f"{uuid4().hex}{suffix}"
    path = root / name
    path.write_bytes(data)
    return str(path)


def save_upload(src_path: str, suffix: str | None = None) -> str:
    root = ensure_storage()
    ext = suffix or Path(src_path).suffix or ""
    dest = root / f"{uuid4().hex}{ext}"
    shutil.copy2(src_path, dest)
    return str(dest)


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def url_sha256(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()
