"""Delete materials and related files from storage."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import PROJECT_ROOT, get_settings
from app.logging_setup import get_logger
from app.models import Material

log = get_logger(__name__)


def _storage_root() -> Path:
    settings = get_settings()
    root = Path(settings.storage_path)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root.resolve()


def _safe_unlink(path: Path, *, root: Path) -> None:
    try:
        resolved = path.resolve()
    except OSError:
        return
    try:
        resolved.relative_to(root)
    except ValueError:
        # Outside storage — only delete if it is under PROJECT_ROOT/data
        data_root = (PROJECT_ROOT / "data").resolve()
        try:
            resolved.relative_to(data_root)
        except ValueError:
            log.warning("skip_delete_outside_storage", path=str(path))
            return
    if resolved.is_file():
        resolved.unlink(missing_ok=True)
    elif resolved.is_dir():
        shutil.rmtree(resolved, ignore_errors=True)


def cleanup_material_files(material: Material) -> None:
    root = _storage_root()
    # Moments / per-material folder
    mat_dir = root / "materials" / material.id
    _safe_unlink(mat_dir, root=root)

    if material.file_path:
        p = Path(material.file_path)
        if not p.is_absolute():
            p = root / p
        _safe_unlink(p, root=root)
        # Companion audio / preview dirs next to media
        stem = p.with_suffix("")
        for companion in (
            p.with_suffix(".mp3"),
            Path(str(stem) + "_preview.mp3"),
            Path(str(stem) + "_frames"),
            Path(str(stem) + "_preview_frames"),
        ):
            _safe_unlink(companion, root=root)


async def delete_material(session: AsyncSession, material_id: str) -> bool:
    m = (
        await session.execute(
            select(Material)
            .options(selectinload(Material.tags))
            .where(Material.id == material_id)
        )
    ).scalars().first()
    if not m:
        return False
    cleanup_material_files(m)
    # Explicitly clear association rows (cascade should handle, but SQLite be safe)
    for link in list(m.tags or []):
        await session.delete(link)
    await session.delete(m)
    await session.commit()
    log.info("material_deleted", material_id=material_id)
    return True


async def delete_materials(session: AsyncSession, ids: list[str]) -> dict:
    unique = []
    seen: set[str] = set()
    for i in ids:
        mid = (i or "").strip()
        if mid and mid not in seen:
            seen.add(mid)
            unique.append(mid)

    deleted: list[str] = []
    missing: list[str] = []
    for mid in unique:
        ok = await delete_material(session, mid)
        if ok:
            deleted.append(mid)
        else:
            missing.append(mid)
    return {"deleted": deleted, "missing": missing, "count": len(deleted)}
