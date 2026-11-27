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
    """Drop explanation lines/paragraphs that invent entities outside card/links/raw_text."""
    from urllib.parse import urlparse

    from app.services.grounding import (
        GroundingContext,
        _token_grounded,
        _tokens,
        entity_grounded,
        extract_entities,
        norm,
    )
    from app.services.resources import digest_lines

    ctx = GroundingContext.from_raw(material.raw_text or "")
    allowed_phrases: set[str] = set()
    allowed_tokens: set[str] = set(ctx.full_tokens)

    def _register_text(value: str | None) -> None:
        raw = (value or "").strip()
        if not raw:
            return
        for ent in extract_entities(raw):
            en = norm(ent)
            if en:
                allowed_phrases.add(en)
                allowed_tokens.update(t for t in _tokens(en) if len(t) >= 3)
        allowed_tokens.update(t for t in _tokens(raw) if len(t) >= 3)

    def _register_url(raw_url: str | None) -> None:
        u = (raw_url or "").strip()
        if not u:
            return
        un = norm(u)
        allowed_phrases.add(un)
        try:
            parsed = urlparse(u)
            host = norm(parsed.hostname or "")
            if host:
                allowed_phrases.add(host)
                bare = host[4:] if host.startswith("www.") else host
                allowed_phrases.add(bare)
                allowed_tokens.add(host)
                allowed_tokens.add(bare)
                for part in re.split(r"[.-]+", bare):
                    if len(part) >= 3:
                        allowed_tokens.add(part)
            for seg in re.split(r"[/._-]+", norm(parsed.path or "")):
                if len(seg) >= 3:
                    allowed_tokens.add(seg)
        except Exception:
            pass

    for t in material.tools or []:
        tn = norm(t or "")
        if tn:
            allowed_phrases.add(tn)
            allowed_tokens.add(tn)
            allowed_tokens.update(tok for tok in _tokens(tn) if len(tok) >= 3)

    _register_url(getattr(material, "source_url", None))
    for item in material.links or []:
        if isinstance(item, dict):
            _register_url(item.get("url"))
            _register_text(item.get("title"))
        elif isinstance(item, str):
            _register_url(item)

    _register_text(getattr(material, "title", None))
    _register_text(getattr(material, "summary", None))
    _register_text(getattr(material, "main_idea", None))
    for line in digest_lines(material.digest or ""):
        _register_text(line)
    for idea in getattr(material, "key_ideas", None) or []:
        _register_text(str(idea))
    for tip in getattr(material, "practical_takeaways", None) or []:
        _register_text(str(tip))

    def _ent_ok(ent: str) -> bool:
        en = norm(ent)
        if len(en) < 4:
            return True
        if en in allowed_phrases or en in allowed_tokens:
            return True
        if entity_grounded(ent, ctx, blob="full"):
            return True
        toks = [t for t in _tokens(en) if len(t) >= 3 and not t.isdigit()]
        if toks and all(_token_grounded(t, allowed_tokens) for t in toks):
            return True
        return False

    def _segment_ok(seg: str) -> bool:
        return all(_ent_ok(ent) for ent in extract_entities(seg))

    _LIST_OR_TABLE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|\|)")
    _HEADING_OR_RULE = re.compile(r"^\s*(?:#{1,6}\s+|---+|\*\*\*+|___+)\s*$")

    kept: list[str] = []
    for block in re.split(r"\n{2,}", (text or "").strip()):
        stripped = block.strip()
        if not stripped:
            continue
        lines = stripped.splitlines()
        if len(lines) == 1 and (_HEADING_OR_RULE.match(stripped) or stripped.startswith("#")):
            kept.append(stripped)
            continue
        if len(lines) > 1 and any(_LIST_OR_TABLE.match(ln) for ln in lines):
            kept_lines: list[str] = []
            for ln in lines:
                s = ln.strip()
                if not s:
                    continue
                if re.fullmatch(r"\|[\s:|-]+\|", s) or s.startswith("#"):
                    kept_lines.append(ln)
                    continue
                if _segment_ok(ln):
                    kept_lines.append(ln)
            # Drop orphan table header+separator if all data rows were dropped
            non_sep = [ln for ln in kept_lines if not re.fullmatch(r"\|[\s:|-]+\|", ln.strip())]
            if len(non_sep) == 1 and non_sep[0].strip().startswith("|"):
                kept_lines = []
            if kept_lines:
                kept.append("\n".join(kept_lines))
            continue
        if _segment_ok(stripped):
            kept.append(stripped)

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
