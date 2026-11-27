"""Trust gate: is this card safe to feed MCP / teach from?"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrustResult:
    score: float
    level: str  # mcp_safe | review | weak
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "level": self.level, "reasons": list(self.reasons)}


def trust_score(
    *,
    raw_text: str | None = None,
    title: str = "",
    tools: list | None = None,
    links: list | None = None,
    digest: str | None = None,
    evidence: list | None = None,
) -> TrustResult:
    """Compute mcp_safe / review / weak from grounded card signals."""
    from app.services.grounding import spoken_blob, tool_is_grounded, GroundingContext
    from app.services.resources import (
        digest_lines,
        is_speech_offtopic,
        is_weak_speech,
        speech_unreliable,
    )

    reasons: list[str] = []
    score = 0.55
    tools = [t for t in (tools or []) if (t or "").strip()]
    links = list(links or [])
    dig_lines = digest_lines(digest or "")
    raw = raw_text or ""
    ctx = GroundingContext.from_raw(raw)
    spoken = spoken_blob(raw)

    kinds = {str(s.get("kind") or "") for s in (evidence or [])}
    if "speech" in kinds:
        score += 0.08
    elif "text" in kinds:
        score += 0.08
    if "ocr" in kinds:
        score += 0.06
    if "speech" in kinds and "ocr" in kinds:
        score += 0.04
        reasons.append("speech+ocr")

    hard_fail = False
    if is_weak_speech(raw) or speech_unreliable(raw, screen_tools=tools):
        score -= 0.18
        reasons.append("weak_or_offtopic_speech")
    if is_speech_offtopic(raw, screen_tools=tools):
        score -= 0.08
        reasons.append("offtopic_speech")

    grounded_tools = [t for t in tools if tool_is_grounded(t, ctx)]
    title_l = (title or "").lower()
    title_hit = any((t or "").lower() in title_l for t in tools if t)
    if grounded_tools or title_hit:
        score += 0.12
        reasons.append("grounded_tools")
    elif re.search(r"[A-Z][a-zA-Z0-9]{2,}", title or "") and not tools:
        score -= 0.15
        reasons.append("product_title_without_tools")
        hard_fail = True

    chrome_like = [
        t
        for t in tools
        if re.match(
            r"^(?:Create\s*PR|Fork\s*You|Code\s*Iss?|Run\s+\S+|YOUR\s+ASSISTANT)\b",
            str(t),
            re.I,
        )
    ]
    if chrome_like:
        score -= 0.2
        reasons.append("chrome_tools")
        hard_fail = True

    if len(dig_lines) >= 2:
        score += 0.1
        reasons.append("digest_ok")
    elif len(dig_lines) == 1:
        score += 0.03
    else:
        score -= 0.12
        reasons.append("empty_digest")

    blob_l = raw.lower()
    github_cue = bool(
        re.search(
            r"github|гитхаб|репозитор|open[- ]?source|опенсорс",
            blob_l,
        )
    )
    gh_links = [
        li
        for li in links
        if "github.com/" in str((li or {}).get("url") or "").lower()
    ]
    dead = [li for li in links if (li or {}).get("verified") == "no"]
    if dead:
        score -= 0.1
        reasons.append("dead_links")
    if github_cue and gh_links:
        score += 0.1
        reasons.append("github_resolved")
    elif github_cue and not gh_links:
        score -= 0.08
        reasons.append("github_unresolved")
        # not hard_fail — empty is honest

    if not spoken or len(spoken) < 40:
        score -= 0.1
        reasons.append("thin_spoken")

    score = round(max(0.0, min(1.0, score)), 3)
    if hard_fail or score < 0.55:
        level = "weak"
    elif score >= 0.82 and not hard_fail:
        level = "mcp_safe"
    else:
        level = "review"
    return TrustResult(score=score, level=level, reasons=reasons)


def apply_trust_to_material(material: Any) -> TrustResult:
    """Set analysis_confidence + trust_level on a Material ORM row."""
    tr = trust_score(
        raw_text=getattr(material, "raw_text", None),
        title=getattr(material, "title", None) or "",
        tools=getattr(material, "tools", None) or [],
        links=getattr(material, "links", None) or [],
        digest=getattr(material, "digest", None),
        evidence=getattr(material, "evidence", None) or [],
    )
    material.analysis_confidence = tr.score
    if hasattr(material, "trust_level"):
        material.trust_level = tr.level
    return tr
