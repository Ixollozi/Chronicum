"""Evidence segments: the grounded source behind a material card.

raw_text stays the flat representation; evidence is the structured truth:
each segment knows its kind, timing and confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TS = re.compile(r"^\[(\d{1,3}):(\d{2})(?:\.(\d+))?\]\s*(.*)$")
_OCR_TS = re.compile(r"^\[OCR\s+(\d{1,3}):(\d{2})(?:\.(\d+))?\]\s*(.*)$")
_SLIDE = re.compile(r"^\[Слайд\s+(\d+)\]\s*(.*)$")
_PDF_PAGE_DIV = re.compile(r"^---\s*Страница\s+\d+(?:\s*/\s*\d+)?(?:\s*\(OCR\))?\s*---$", re.I)


@dataclass
class EvidenceSegment:
    kind: str  # speech | ocr | caption | slide | vision | text
    text: str
    t_start: float | None = None
    t_end: float | None = None
    confidence: float | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "text": self.text,
            "t_start": self.t_start,
            "t_end": self.t_end,
            "confidence": self.confidence,
        }


def _sec(m: re.Match) -> float:
    return int(m.group(1)) * 60 + int(m.group(2)) + (int(m.group(3) or 0) / 10.0)


def build_evidence(raw_text: str | None, *, source_type: str = "") -> list[dict]:
    """Parse flat raw_text into evidence segments (deterministic, offline)."""
    text = raw_text or ""
    if not text.strip():
        return []
    segs: list[EvidenceSegment] = []
    in_ocr = False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[On-screen"):
            in_ocr = True
            continue
        if _PDF_PAGE_DIV.match(s):
            continue
        if s.startswith("[Vision]"):
            segs.append(EvidenceSegment("vision", s[len("[Vision]") :].strip(), confidence=0.4))
            continue
        m = _SLIDE.match(s)
        if m:
            segs.append(EvidenceSegment("slide", m.group(2).strip(), confidence=0.6))
            continue
        m = _OCR_TS.match(s)
        if m:
            segs.append(EvidenceSegment("ocr", m.group(4).strip(), t_start=_sec(m), confidence=0.7))
            continue
        m = _TS.match(s)
        if m and not in_ocr:
            segs.append(EvidenceSegment("speech", m.group(4).strip(), t_start=_sec(m), confidence=0.8))
            continue
        kind = "ocr" if in_ocr else ("text" if source_type in {"article", "pdf", "note"} else "caption")
        segs.append(EvidenceSegment(kind, s, confidence=0.65 if kind == "text" else 0.5))
    return [s.to_dict() for s in segs if s.text]


def evidence_blob(evidence: list[dict] | None) -> str:
    return "\n".join(str(s.get("text") or "") for s in (evidence or []))


def analysis_confidence(raw_text: str | None, evidence: list[dict] | None = None) -> float:
    """0..1 confidence of the analysis itself (not material quality)."""
    ev = evidence if evidence is not None else build_evidence(raw_text)
    if not ev:
        return 0.0
    total_chars = sum(len(str(s.get("text") or "")) for s in ev)
    if total_chars < 40:
        return 0.15
    weighted = 0.0
    weight = 0
    for s in ev:
        conf = s.get("confidence")
        conf = 0.5 if conf is None else float(conf)
        n = max(1, len(str(s.get("text") or "")))
        weighted += conf * n
        weight += n
    base = weighted / max(1, weight)
    kinds = {s.get("kind") for s in ev}
    if "speech" in kinds and "ocr" in kinds:
        base = min(1.0, base + 0.1)
    return round(max(0.0, min(1.0, base)), 3)


def has_evidence(raw_text: str | None, evidence: list[dict] | None = None) -> bool:
    ev = evidence if evidence is not None else build_evidence(raw_text)
    return sum(len(str(s.get("text") or "")) for s in ev) >= 40
