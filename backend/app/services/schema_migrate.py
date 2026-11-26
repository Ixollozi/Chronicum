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
    "code_blocks": "JSON",
    "entry_kind": "TEXT DEFAULT 'knowledge'",
    "analysis_confidence": "FLOAT",
    "trust_level": "TEXT",
    "evidence": "JSON",
    "locked_fields": "JSON",
}

CLIENT_EXTRA_COLUMNS: dict[str, str] = {
    "canon_facts": "TEXT",
    "summary_up_to_message_id": "VARCHAR(36)",
    "user_id": "VARCHAR(36)",
}

# materials.user_id was INTEGER (telegram); SQLite affinity accepts UUID strings without rewrite.
MATERIAL_USER_ID_NOTE = "user_id stored as TEXT affinity for Google account UUIDs"

CLIENT_MESSAGE_EXTRA_COLUMNS: dict[str, str] = {
    "speaker": "VARCHAR(20) DEFAULT 'unknown'",
    "speaker_name": "VARCHAR(200)",
    "kind": "VARCHAR(20) DEFAULT 'text'",
    "event_at": "DATETIME",
    "content_hash": "VARCHAR(64)",
}


async def _add_missing(conn: AsyncConnection, table: str, columns: dict[str, str]) -> None:
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).mappings().all()
    if not rows:
        return
    existing = {r["name"] for r in rows}
    for col, typ in columns.items():
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))


async def ensure_schema(conn: AsyncConnection) -> None:
    from app.db import Base

    await conn.run_sync(Base.metadata.create_all)
    await _add_missing(conn, "materials", MATERIAL_EXTRA_COLUMNS)
    await _add_missing(conn, "clients", CLIENT_EXTRA_COLUMNS)
    await _add_missing(conn, "client_messages", CLIENT_MESSAGE_EXTRA_COLUMNS)
