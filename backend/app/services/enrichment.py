"""On-demand digest / explain / evaluate + helpers after primary card."""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import _extract_json, with_llm_fallback
from app.models import Material


def _material_context(m: Material, *, for_teacher: bool = False) -> str:
    """Card context for enrichment. Teacher mode omits raw_text as fact source."""
    parts = [
        f"Title: {m.title or ''}",
        f"Type: {m.source_type}",
        f"URL: {m.source_url or ''}",
        f"Summary: {m.summary or ''}",
        f"Main idea: {m.main_idea or ''}",
        f"Digest: {m.digest or ''}",
        f"Key ideas: {m.key_ideas or []}",
        f"Takeaways: {m.practical_takeaways or []}",
        f"Tools (only these product names are allowed): {m.tools or []}",
        f"Links (only these URLs are allowed): {m.links or []}",
        f"Score: {m.usefulness_score} — {m.usefulness_reason or ''}",
        f"Trust: {getattr(m, 'trust_level', None)} / {getattr(m, 'analysis_confidence', None)}",
    ]
    if for_teacher:
        moments = getattr(m, "key_moments", None) or []
        if moments:
            parts.append(f"Key moments: {moments[:8]}")
        parts.append(
            "RULES: Do not invent tools, repos, numbers, or steps absent from "
            "Digest/Tools/Links/Moments above. If unknown, say so."
        )
    else:
        parts.append(f"Raw (truncated):\n{(m.raw_text or '')[:20000]}")
    return "\n".join(parts)


def _ground_explanation(text: str, material: Material) -> str:
    """Drop explanation paragraphs that invent entities outside tools/digest/speech."""
    from app.services.grounding import GroundingContext, extract_entities, entity_grounded
    from app.services.resources import digest_lines

    ctx = GroundingContext.from_raw(material.raw_text or "")
    allowed: set[str] = set()
    for t in material.tools or []:
        allowed.add((t or "").lower())
        for part in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{4,}", t or ""):
            allowed.add(part.lower())
    for line in digest_lines(material.digest or ""):
        for ent in extract_entities(line):
            allowed.add(ent.lower())
    kept: list[str] = []
    for block in re.split(r"\n{2,}", (text or "").strip()):
        bad = False
        for ent in extract_entities(block):
            en = ent.lower()
            if en in allowed or entity_grounded(ent, ctx, blob="spoken"):
                continue
            if len(en) < 4:
                continue
            bad = True
            break
        if not bad and block.strip():
            kept.append(block.strip())
    return "\n\n".join(kept) if kept else (text or "").strip()


async def rebuild_digest(session: AsyncSession, material: Material) -> Material:
    system = (
        "You create ultra-short digests for lazy readers. "
        "Respond in the language of the material (usually Russian). "
        "Return plain text only: 3-6 short bullet lines, max ~800 characters. No intro. "
        "CTA/lead magnets (comment keywords, subscribe) are NOT takeaways."
    )
    user = _material_context(material)

    async def _run(client):
        return await client.complete_text(system, user)

    text = await with_llm_fallback(session, _run)
    if "digest" not in set(material.locked_fields or []):
        from app.services.resources import ground_digest_lines

        material.digest = ground_digest_lines(
            (text or "").strip(), tools=list(material.tools or []), raw_text=material.raw_text
        )
    await session.commit()
    await session.refresh(material)
    return material


async def build_explanation(session: AsyncSession, material: Material) -> Material:
    system = (
        "You are a clear teacher. Write a detailed but structured explanation "
        "for someone who skipped the source. Use Russian if the source is Russian. "
        "Respond in Markdown: ## headings, lists, **bold**, and fenced code blocks "
        "with language tags whenever commands appear. "
        "Sections: ## Суть, ## Шаги, ## Зачем, ## Как применить, ## Ограничения, ## Источники. "
        "Sources may only cite URLs from the Links list. No JSON wrapper."
    )
    user = _material_context(material, for_teacher=True)

    async def _run(client):
        return await client.complete_text(system, user)

    text = await with_llm_fallback(session, _run)
    material.detailed_explanation = _ground_explanation((text or "").strip(), material)
    await session.commit()
    await session.refresh(material)
    return material


async def build_evaluation(session: AsyncSession, material: Material) -> Material:
    system = (
        "Give an objective evaluation of this knowledge item. "
        "Return ONLY valid JSON (no markdown): "
        '{"pros":["..."],"cons":["..."],"risks":["..."],"best_for":"...","verdict":"...","score_agreement":1-10}'
        " Language: Russian if source is Russian."
    )
    user = _material_context(material)

    async def _run(client):
        raw = await client.complete_text(system, user)
        return _extract_json(raw)

    data = await with_llm_fallback(session, _run)
    material.objective_evaluation = data
    await session.commit()
    await session.refresh(material)
    return material
