"""On-demand digest / explain / evaluate + helpers after primary card."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import _extract_json, with_llm_fallback
from app.models import Material


def _material_context(m: Material) -> str:
    parts = [
        f"Title: {m.title or ''}",
        f"Type: {m.source_type}",
        f"URL: {m.source_url or ''}",
        f"Summary: {m.summary or ''}",
        f"Main idea: {m.main_idea or ''}",
        f"Digest: {m.digest or ''}",
        f"Key ideas: {m.key_ideas or []}",
        f"Takeaways: {m.practical_takeaways or []}",
        f"Score: {m.usefulness_score} — {m.usefulness_reason or ''}",
        f"Raw (truncated):\n{(m.raw_text or '')[:20000]}",
    ]
    return "\n".join(parts)


async def rebuild_digest(session: AsyncSession, material: Material) -> Material:
    system = (
        "You create ultra-short digests for lazy readers. "
        "Respond in the language of the material (usually Russian). "
        "Return plain text only: 3-6 short bullet lines, max ~800 characters. No intro."
    )
    user = _material_context(material)

    async def _run(client):
        return await client.complete_text(system, user)

    text = await with_llm_fallback(session, _run)
    material.digest = text.strip()
    await session.commit()
    await session.refresh(material)
    return material


async def build_explanation(session: AsyncSession, material: Material) -> Material:
    system = (
        "You are a clear teacher. Write a detailed but structured explanation of the material "
        "for someone who skipped the source. Use Russian if the source is Russian. "
        "Respond in Markdown: ## headings, lists, **bold**, and fenced code blocks with language tags "
        "(```python, ```js, ```bash, …) whenever code/commands appear. "
        "Sections: Суть, Как это работает, Примеры/кейсы, Как применить, На что обратить внимание. "
        "No JSON wrapper."
    )
    user = _material_context(material)

    async def _run(client):
        return await client.complete_text(system, user)

    text = await with_llm_fallback(session, _run)
    material.detailed_explanation = text.strip()
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
