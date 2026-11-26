"""Per-user row scoping for materials / clients."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.sql import ColumnElement

from app.api.deps import AuthContext
from app.models import Client, Material


def owner_filter(column: ColumnElement, auth: AuthContext):
    """Admin sees all (incl. orphans). Users see only their rows. None = no extra WHERE."""
    if auth.is_admin:
        return None
    if auth.user_id:
        return column == auth.user_id
    from sqlalchemy import false

    return false()


def ensure_material_access(m: Material | None, auth: AuthContext) -> Material:
    if not m:
        raise HTTPException(404, "Not found")
    if auth.is_admin:
        return m
    if auth.user_id and m.user_id == auth.user_id:
        return m
    raise HTTPException(404, "Not found")


def ensure_client_access(c: Client | None, auth: AuthContext) -> Client:
    if not c:
        raise HTTPException(404, "Not found")
    if auth.is_admin:
        return c
    if auth.user_id and c.user_id == auth.user_id:
        return c
    raise HTTPException(404, "Not found")
