"""Dense timed on-screen OCR atlas (local Tesseract / OpenCV — free)."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from app.logging_setup import get_logger
from app.services.media import extract_frame_at, probe_duration_seconds, score_frame_file

log = get_logger(__name__)

OCR_BLOCK_HEADER = "[On-screen OCR timed]"
_OCR_LINE = re.compile(
    r"^\[OCR\s+(\d{1,3}):(\d{2})(?:\.(\d+))?\]\s*(.*)$",
    re.I,
)


@dataclass
class AtlasFrame:
    t_sec: float
    text: str
    score: float
    path: str | None = None

    @property
    def tokens(self) -> list[str]:
        return re.findall(r"[A-Za-zА-Яа-яЁё0-9#]{2,}", self.text or "")


def atlas_sampling(*, duration: float, source_type: str) -> tuple[float, int]:
    """Return (every_seconds, max_frames)."""
    dur = max(0.0, float(duration or 0))
    short_or_ig = source_type in {"instagram", "tiktok"} or dur <= 180
    if short_or_ig:
        every = 2.0
        max_frames = 45
    elif dur <= 600:
        every = 5.0
        max_frames = 40
    else:
        every = 8.0
        max_frames = 36
    if dur > 0:
        # Never sample more densely than needed for length
        est = int(dur / every) + 1
        if est < max_frames:
            max_frames = max(6, est)
    return every, max_frames


def format_ocr_timestamp(t_sec: float) -> str:
    t = max(0.0, float(t_sec))
    m = int(t // 60)
    s = int(t % 60)
    frac = int(round((t - int(t)) * 10))
    if frac:
        return f"{m}:{s:02d}.{frac}"
    return f"{m}:{s:02d}"


def format_timed_ocr_block(frames: list[AtlasFrame]) -> str:
    if not frames:
        return ""
    from app.services.local_ocr import ocr_sanitize

    lines = [OCR_BLOCK_HEADER]
    for fr in frames:
        text = ocr_sanitize(fr.text or "")
        if not text:
            continue
        # Skip digit-only noise; keep brands and percent lines for harvest
        if re.fullmatch(r"[\d\s/+×x-]+", text):
            continue
        if re.fullmatch(r"(?i)report\s*card", text):
            continue
        lines.append(f"[OCR {format_ocr_timestamp(fr.t_sec)}] {text}")
    return "\n".join(lines) if len(lines) > 1 else ""


def parse_timed_ocr_block(raw_text: str | None) -> list[AtlasFrame]:
    """Parse [OCR m:ss] lines from raw_text (no image paths)."""
    if not raw_text:
        return []
    out: list[AtlasFrame] = []
    in_block = False
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(OCR_BLOCK_HEADER) or stripped.startswith("[On-screen text OCR"):
            in_block = True
            continue
        if not in_block:
            # Also accept OCR lines anywhere
            pass
        m = _OCR_LINE.match(stripped)
        if not m:
            if in_block and stripped.startswith("[") and not stripped.upper().startswith("[OCR"):
                # next section
                in_block = False
            continue
        start = int(m.group(1)) * 60 + int(m.group(2))
        if m.group(3):
            start += int(m.group(3)) / (10 ** len(m.group(3)))
        text = (m.group(4) or "").strip()
        if text:
            out.append(AtlasFrame(t_sec=float(start), text=text, score=float(len(text))))
    return out


def _ocr_image(path: str) -> str:
    from app.services.local_ocr import ocr_image_file

    return ocr_image_file(path)


def save_atlas_json(out_dir: Path, frames: list[AtlasFrame]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "frames": [
            {
                "t_sec": fr.t_sec,
                "text": fr.text,
                "score": fr.score,
                "path": fr.path,
            }
            for fr in frames
        ]
    }
    path = out_dir / "atlas.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_atlas_json(path: Path | str) -> list[AtlasFrame]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    from app.services.local_ocr import ocr_sanitize

    frames: list[AtlasFrame] = []
    for item in data.get("frames") or []:
        text = ocr_sanitize(str(item.get("text") or "").strip())
        if not text:
            # Keep file for visual moment, empty caption filled from speech later
            text = ""
        frames.append(
            AtlasFrame(
                t_sec=float(item.get("t_sec") or 0),
                text=text,
                score=float(item.get("score") or 0),
                path=item.get("path"),
            )
        )
    return frames


def strip_ocr_blocks(raw_text: str | None) -> str:
    """Remove timed/legacy OCR sections from raw_text (speech remains)."""
    if not raw_text:
        return ""
    lines = []
    skipping = False
    for line in raw_text.splitlines():
        s = line.strip()
        if s.startswith(OCR_BLOCK_HEADER) or s.startswith("[On-screen text OCR"):
            skipping = True
            continue
        if skipping:
            if _OCR_LINE.match(s) or not s:
                continue
            if s.startswith("[") and not s.upper().startswith("[OCR"):
                skipping = False
                lines.append(line)
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def merge_speech_with_ocr(raw_text: str | None, frames: list[AtlasFrame]) -> str:
    speech = strip_ocr_blocks(raw_text)
    block = format_timed_ocr_block(frames)
    if speech and block:
        return f"{speech}\n\n{block}"
    return speech or block or ""


async def build_screen_atlas(
    video_path: str,
    out_dir: str | Path,
    *,
    duration: float | None = None,
    source_type: str = "video",
    keep_top_n: int = 24,
) -> list[AtlasFrame]:
    """Sample video, OCR frames, keep top-N OCR-rich stills + full text list."""
    vpath = str(video_path)
    if not Path(vpath).exists():
        return []

    dur = float(duration or 0) or await probe_duration_seconds(vpath)
    every, max_frames = atlas_sampling(duration=dur, source_type=source_type)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    timestamps: list[float] = []
    if dur <= 0:
        timestamps = [0.5, 2.0, 4.0, 6.0, 8.0]
    else:
        t = min(0.4, max(0.15, dur * 0.02))
        while t < max(0.0, dur - 0.25) and len(timestamps) < max_frames:
            timestamps.append(round(t, 2))
            t += every

    ranked: list[AtlasFrame] = []
    for i, t in enumerate(timestamps):
        fname = f"frame_{i:04d}_t{int(t)}.jpg"
        fpath = str(out / fname)
        ok = await extract_frame_at(vpath, t, fpath)
        if not ok:
            continue
        text = await asyncio.to_thread(_ocr_image, fpath)
        score = await asyncio.to_thread(score_frame_file, fpath)
        from app.services.local_ocr import ocr_sanitize

        text = ocr_sanitize(text)
        if text:
            # Prefer readable text density
            score += min(120.0, len(text) * 0.9 + len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", text)) * 5.0)
        else:
            score *= 0.15
        if score < 8 and len(text) < 4:
            try:
                Path(fpath).unlink(missing_ok=True)
            except Exception:
                pass
            continue
        ranked.append(AtlasFrame(t_sec=float(t), text=text, score=float(score), path=fpath))

    # Dedupe near-identical OCR within 1.6s — keep higher score
    ranked.sort(key=lambda f: f.t_sec)
    kept: list[AtlasFrame] = []
    for fr in ranked:
        if not fr.text and fr.score < 20:
            continue
        if kept and abs(fr.t_sec - kept[-1].t_sec) < 1.6:
            prev = kept[-1]
            # similar text?
            a = set(fr.tokens)
            b = set(prev.tokens)
            if a and b and len(a & b) / max(1, len(a | b)) > 0.72:
                if fr.score > prev.score:
                    # replace
                    try:
                        if prev.path:
                            Path(prev.path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    kept[-1] = fr
                else:
                    try:
                        if fr.path:
                            Path(fr.path).unlink(missing_ok=True)
                    except Exception:
                        pass
                continue
        kept.append(fr)

    # Keep top-N by score as files; drop weaker file paths but keep text in list
    by_score = sorted(kept, key=lambda f: f.score, reverse=True)
    keep_paths = {id(f) for f in by_score[:keep_top_n]}
    for fr in kept:
        if id(fr) not in keep_paths and fr.path:
            try:
                Path(fr.path).unlink(missing_ok=True)
            except Exception:
                pass
            fr.path = None

    save_atlas_json(out, kept)
    log.info(
        "screen_atlas_built",
        frames=len(kept),
        with_files=sum(1 for f in kept if f.path),
        every=every,
        duration=dur,
        source_type=source_type,
    )
    return kept


def find_atlas_dir_for_media(media_path: str | None, material_atlas: str | Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if material_atlas:
        candidates.append(Path(material_atlas))
    if media_path:
        p = Path(media_path)
        candidates.append(p.with_name(p.stem + "_atlas"))
        candidates.append(p.parent / "atlas")
    for c in candidates:
        if (c / "atlas.json").exists():
            return c
    return None


def load_atlas_for_material(
    *,
    raw_text: str | None,
    media_path: str | None,
    material_atlas_dir: str | Path | None = None,
) -> list[AtlasFrame]:
    """Prefer atlas.json (with paths), else parse timed OCR from raw_text."""
    d = find_atlas_dir_for_media(media_path, material_atlas_dir)
    if d:
        frames = load_atlas_json(d / "atlas.json")
        if frames:
            return frames
    return parse_timed_ocr_block(raw_text)
