"""Telegram inline keyboards for Chronicum bot."""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardMarkup


def material_keyboard_dict(
    material_id: str, *, awaiting_full: bool = False, failed: bool = False
) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    if failed:
        rows.append([{"text": "Retry", "callback_data": f"rt:{material_id}"}])
    elif awaiting_full:
        rows.append([{"text": "Process full", "callback_data": f"pf:{material_id}"}])
        rows.append([{"text": "Retry", "callback_data": f"rt:{material_id}"}])
    else:
        rows.append([{"text": "Retry", "callback_data": f"rt:{material_id}"}])
    return {"inline_keyboard": rows}


def pending_tags_keyboard_dict(
    tags: list[tuple[int, str]], *, limit: int = 8
) -> dict[str, Any] | None:
    rows: list[list[dict[str, str]]] = []
    for tag_id, name in tags[:limit]:
        label = name if len(name) <= 28 else name[:27] + "..."
        rows.append(
            [
                {"text": f"OK {label}", "callback_data": f"at:{tag_id}"},
                {"text": "X", "callback_data": f"rj:{tag_id}"},
            ]
        )
    if not rows:
        return None
    return {"inline_keyboard": rows}


def as_markup(data: dict[str, Any] | None) -> InlineKeyboardMarkup | None:
    if not data:
        return None
    return InlineKeyboardMarkup.model_validate(data)


def material_keyboard(
    material_id: str, *, awaiting_full: bool = False, failed: bool = False
) -> InlineKeyboardMarkup:
    return as_markup(material_keyboard_dict(material_id, awaiting_full=awaiting_full, failed=failed))  # type: ignore[return-value]


def pending_tags_keyboard(
    tags: list[tuple[int, str]], *, limit: int = 8
) -> InlineKeyboardMarkup | None:
    return as_markup(pending_tags_keyboard_dict(tags, limit=limit))


def proposed_tags_from_material(material) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for mt in getattr(material, "tags", []) or []:
        if mt.proposed and mt.tag:
            out.append((mt.tag.id, mt.tag.name))
    return out
