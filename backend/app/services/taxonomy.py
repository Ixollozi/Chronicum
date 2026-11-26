"""Canonical taxonomy: normalize + alias + resolve tags/categories (no duplicates)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models import Category, Material, MaterialTag, Tag, TagStatus

log = get_logger(__name__)

# Alias key (already normalized) → canonical key
TAG_ALIASES: dict[str, str] = {
    "капкат": "capcut",
    "cap cut": "capcut",
    "cap-cut": "capcut",
    "кап кат": "capcut",
    "llm": "llm",
    "llms": "llm",
    "large language model": "llm",
    "large language models": "llm",
    "gpt": "gpt",
    "chatgpt": "chatgpt",
    "chat gpt": "chatgpt",
    "ai agent": "ai agents",
    "ai агент": "ai agents",
    "ai агенты": "ai agents",
    "агенты ai": "ai agents",
    "reels": "reels",
    "рилс": "reels",
    "рилсы": "reels",
    "instagram reels": "reels",
    "тик ток": "tiktok",
    "tik tok": "tiktok",
    "youtube shorts": "youtube shorts",
    "шортс": "youtube shorts",
    "видеомонтаж": "видеомонтаж",
    "монтаж видео": "видеомонтаж",
    "анимация текста": "анимация текста",
    "text animation": "анимация текста",
}

TAG_DISPLAY: dict[str, str] = {
    "capcut": "CapCut",
    "llm": "LLM",
    "gpt": "GPT",
    "chatgpt": "ChatGPT",
    "ai agents": "AI agents",
    "reels": "Reels",
    "tiktok": "TikTok",
    "youtube shorts": "YouTube Shorts",
    "ai": "AI",
}

CATEGORY_ALIASES: dict[str, str] = {
    "ии": "ai",
    "искусственный интеллект": "ai",
    "artificial intelligence": "ai",
    "dev": "программирование",
    "development": "программирование",
    "coding": "программирование",
    "code": "программирование",
    "programming": "программирование",
    "marketing": "маркетинг",
    "sales": "продажи",
    "design": "дизайн",
    "ui": "дизайн",
    "ux": "дизайн",
    "business": "бизнес",
    "freelance": "фриланс",
    "games": "игры",
    "gaming": "игры",
    "none": "",
    "n/a": "",
    "other": "",
    "другое": "",
    "без категории": "",
}

CATEGORY_DISPLAY: dict[str, str] = {
    "ai": "AI",
    "маркетинг": "Маркетинг",
    "продажи": "Продажи",
    "программирование": "Программирование",
    "дизайн": "Дизайн",
    "бизнес": "Бизнес",
    "фриланс": "Фриланс",
    "игры": "Игры",
}


def normalize_key(name: str) -> str:
    """Stable key for equality: casefold, ё→е, strip junk, collapse spaces, apply aliases."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)
    s = s.replace("ё", "е").replace("Ё", "Е")
    s = s.casefold().strip()
    s = s.lstrip("#").strip()
    s = s.replace("—", " ").replace("–", "-")
    s = re.sub(r"[-_/·•|+]+", " ", s)
    s = re.sub(r"[^\w\s]+", " ", s, flags=re.UNICODE)
    s = " ".join(s.split())
    if not s:
        return ""
    s = TAG_ALIASES.get(s, s)
    compact = s.replace(" ", "")
    if compact in TAG_ALIASES:
        s = TAG_ALIASES[compact]
    return s


def normalize_category_key(name: str) -> str:
    key = normalize_key(name)
    if not key:
        return ""
    return CATEGORY_ALIASES.get(key, key)


def display_tag_name(raw: str, key: str | None = None) -> str:
    k = key if key is not None else normalize_key(raw)
    if k in TAG_DISPLAY:
        return TAG_DISPLAY[k]
    cleaned = " ".join((raw or "").strip().lstrip("#").split())
    return (cleaned[:100] if cleaned else k[:100]) or k


def display_category_name(raw: str, key: str | None = None) -> str:
    k = key if key is not None else normalize_category_key(raw)
    if not k:
        return ""
    if k in CATEGORY_DISPLAY:
        return CATEGORY_DISPLAY[k]
    cleaned = " ".join((raw or "").strip().split())
    if cleaned:
        return cleaned[:1].upper() + cleaned[1:]
    return k[:1].upper() + k[1:]


@dataclass
class TaxonomyMaps:
    categories: list[str]
    approved_tags: list[str]
    cat_by_key: dict[str, Category]
    tag_by_key: dict[str, Tag]


async def load_taxonomy(session: AsyncSession) -> TaxonomyMaps:
    cats = (await session.execute(select(Category))).scalars().all()
    tags = (await session.execute(select(Tag))).scalars().all()
    cat_by_key: dict[str, Category] = {}
    for c in cats:
        cat_by_key[normalize_category_key(c.name)] = c
    tag_by_key: dict[str, Tag] = {}
    for t in tags:
        tag_by_key[normalize_key(t.name)] = t
    approved = sorted(
        {t.name for t in tags if t.status == TagStatus.APPROVED.value},
        key=str.casefold,
    )
    return TaxonomyMaps(
        categories=[c.name for c in cats],
        approved_tags=approved,
        cat_by_key=cat_by_key,
        tag_by_key=tag_by_key,
    )


async def resolve_category(
    session: AsyncSession,
    raw: str,
    cat_by_key: dict[str, Category],
    *,
    allow_create: bool = True,
) -> Category | None:
    key = normalize_category_key(raw)
    if not key:
        return None

    if key in cat_by_key:
        return cat_by_key[key]

    # Compact / substring soft match against known categories only
    compact = key.replace(" ", "")
    for ck, cat in cat_by_key.items():
        if not ck:
            continue
        if compact == ck.replace(" ", "") or key in ck or ck in key:
            return cat

    if not allow_create:
        return None

    name = display_category_name(raw, key)[:100]
    cat = Category(name=name)
    session.add(cat)
    await session.flush()
    cat_by_key[key] = cat
    log.info("taxonomy_category_created", name=name, key=key)
    return cat


async def resolve_tag(
    session: AsyncSession,
    raw: str,
    tag_by_key: dict[str, Tag],
    *,
    allow_create: bool = True,
    auto_approve: bool = True,
) -> Tag | None:
    key = normalize_key(raw)
    if not key:
        return None

    existing = tag_by_key.get(key)
    if existing:
        if existing.status == TagStatus.REJECTED.value:
            return None
        if auto_approve and existing.status != TagStatus.APPROVED.value:
            existing.status = TagStatus.APPROVED.value
        return existing

    compact = key.replace(" ", "")
    for tk, tag in tag_by_key.items():
        if tk.replace(" ", "") == compact:
            if tag.status == TagStatus.REJECTED.value:
                return None
            if auto_approve and tag.status != TagStatus.APPROVED.value:
                tag.status = TagStatus.APPROVED.value
            tag_by_key[key] = tag
            return tag

    if not allow_create:
        return None

    name = display_tag_name(raw, key)[:100]
    status = TagStatus.APPROVED.value if auto_approve else TagStatus.PENDING.value
    tag = Tag(name=name, status=status)
    session.add(tag)
    await session.flush()
    tag_by_key[key] = tag
    log.info("taxonomy_tag_created", name=name, key=key, status=status)
    return tag


async def merge_duplicate_taxonomy(
    session: AsyncSession,
    *,
    promote_pending: bool = False,
) -> dict[str, int]:
    """One-shot cleanup: merge tags/categories that share the same normalize key."""
    tags = (await session.execute(select(Tag).order_by(Tag.id))).scalars().all()
    by_key: dict[str, list[Tag]] = {}
    for t in tags:
        by_key.setdefault(normalize_key(t.name), []).append(t)

    tags_merged = 0
    with session.no_autoflush:
        for key, group in by_key.items():
            if not key or len(group) < 2:
                continue
            has_approved = any(t.status == TagStatus.APPROVED.value for t in group)
            has_pending = any(t.status == TagStatus.PENDING.value for t in group)
            group.sort(
                key=lambda t: (
                    0
                    if t.status == TagStatus.APPROVED.value
                    else 1
                    if t.status == TagStatus.PENDING.value
                    else 2,
                    t.id,
                )
            )
            # Prefer the row already holding the canonical display name
            target_name = display_tag_name(group[0].name, key)[:100]
            group.sort(
                key=lambda t: (
                    0 if t.name == target_name else 1,
                    0
                    if t.status == TagStatus.APPROVED.value
                    else 1
                    if t.status == TagStatus.PENDING.value
                    else 2,
                    t.id,
                )
            )
            keep = group[0]
            if has_approved:
                keep.status = TagStatus.APPROVED.value
            elif has_pending:
                keep.status = TagStatus.PENDING.value
            keep_is_pending = keep.status == TagStatus.PENDING.value
            for dup in group[1:]:
                links = (
                    await session.execute(select(MaterialTag).where(MaterialTag.tag_id == dup.id))
                ).scalars().all()
                for link in links:
                    clash = (
                        await session.execute(
                            select(MaterialTag).where(
                                MaterialTag.material_id == link.material_id,
                                MaterialTag.tag_id == keep.id,
                            )
                        )
                    ).scalars().first()
                    if clash:
                        await session.delete(link)
                    else:
                        link.tag_id = keep.id
                        link.proposed = keep_is_pending
                await session.delete(dup)
                tags_merged += 1
                log.info("taxonomy_tag_merged", keep=keep.name, removed=dup.name, key=key)
            await session.flush()
            if keep.name != target_name:
                # Avoid UNIQUE race: park under temp name if needed
                clash = (
                    await session.execute(select(Tag).where(Tag.name == target_name, Tag.id != keep.id))
                ).scalars().first()
                if clash:
                    await session.delete(clash)
                    await session.flush()
                keep.name = target_name

        cats = (await session.execute(select(Category).order_by(Category.id))).scalars().all()
        cat_groups: dict[str, list[Category]] = {}
        for c in cats:
            cat_groups.setdefault(normalize_category_key(c.name), []).append(c)

        cats_merged = 0
        for junk in cat_groups.get("", []):
            mats = (
                await session.execute(select(Material).where(Material.category_id == junk.id))
            ).scalars().all()
            for m in mats:
                m.category_id = None
            await session.delete(junk)
            cats_merged += 1
            log.info("taxonomy_category_dropped", name=junk.name)

        for key, group in cat_groups.items():
            if not key or len(group) < 2:
                continue
            group.sort(key=lambda c: c.id)
            keep = group[0]
            target_name = display_category_name(keep.name, key)[:100]
            for dup in group[1:]:
                mats = (
                    await session.execute(select(Material).where(Material.category_id == dup.id))
                ).scalars().all()
                for m in mats:
                    m.category_id = keep.id
                await session.delete(dup)
                cats_merged += 1
                log.info("taxonomy_category_merged", keep=keep.name, removed=dup.name, key=key)
            if keep.name != target_name:
                keep.name = target_name

        promoted = 0
        if promote_pending:
            pending = (
                await session.execute(select(Tag).where(Tag.status == TagStatus.PENDING.value))
            ).scalars().all()
            for t in pending:
                t.status = TagStatus.APPROVED.value
                promoted += 1
            if promoted:
                links = (
                    await session.execute(select(MaterialTag).where(MaterialTag.proposed.is_(True)))
                ).scalars().all()
                for link in links:
                    link.proposed = False

    await session.flush()
    return {"tags_merged": tags_merged, "categories_merged": cats_merged, "pending_promoted": promoted}
