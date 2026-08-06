"""Lite SQLite: create_all + additive ALTER for new columns."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# column_name -> SQL type fragment for SQLite ALTER
MATERIAL_EXTRA_COLUMNS: dict[str, str] = {
    "digest": "TEXT",
    "detailed_explanation": "TEXT",
    "objective_evaluation": "JSON",
    "ai_opinion": "TEXT",
    "ai_opinion_sources": "JSON",
    "ai_opinion_at": "DATETIME",
    "key_moments": "JSON",
}


async def ensure_schema(conn: AsyncConnection) -> None:
    from app.db import Base

    await conn.run_sync(Base.metadata.create_all)

    rows = (await conn.execute(text("PRAGMA table_info(materials)"))).mappings().all()
    existing = {r["name"] for r in rows}
    for col, typ in MATERIAL_EXTRA_COLUMNS.items():
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE materials ADD COLUMN {col} {typ}"))
