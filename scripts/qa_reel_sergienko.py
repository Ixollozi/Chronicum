#!/usr/bin/env python3
"""Reprocess Sergienko reel and assert card/moments quality gates."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

REF_URL = "https://www.instagram.com/p/DbtAgZMswgi/"
REF_ID = "c9553bc9-111c-4aaa-a5b2-b7f9df1fdfff"

STOCK = ("pexels", "storyblocks", "envato")
NLE = ("premiere", "davinci", "final cut")


def _norm(s: str) -> str:
    return (s or "").lower()


def assert_card(m: dict) -> list[str]:
    errors: list[str] = []
    tools_blob = " ".join(m.get("tools") or []).lower()
    links_blob = " ".join(m.get("links") or []).lower()
    digest = m.get("digest") or ""
    ideas = m.get("key_ideas") or []
    raw = m.get("raw_text") or ""

    if "[on-screen ocr timed]" not in raw.lower() and "[ocr " not in raw.lower():
        errors.append("raw_text missing timed OCR block")

    if not any(n in tools_blob for n in NLE):
        errors.append(f"tools missing NLE (Premiere/DaVinci/Final Cut): {m.get('tools')}")

    stock_hit = any(s in tools_blob or s in links_blob or s in digest.lower() for s in STOCK)
    if not stock_hit:
        errors.append("tools/links/digest missing stock brands (Pexels/Storyblocks/Envato)")

    # Digest vs key_ideas overlap
    from app.services.resources import digest_lines, jaccard, token_set

    dig_lines = digest_lines(digest)
    dig_sets = [token_set(x) for x in dig_lines]
    max_ov = 0.0
    for idea in ideas:
        for ds in dig_sets:
            max_ov = max(max_ov, jaccard(token_set(str(idea)), ds))
    if dig_lines and ideas and max_ov >= 0.55:
        errors.append(f"digest↔key_ideas overlap too high ({max_ov:.2f})")

    tool_mentions = sum(1 for t in (m.get("tools") or []) if t.lower() in digest.lower())
    # Also count known brands in digest even if tools naming differs
    brand_in_digest = sum(1 for b in ("premiere", "pexels", "envato", "storyblocks", "davinci", "chatgpt") if b in digest.lower())
    if brand_in_digest < 2 and tool_mentions < 2:
        errors.append("digest should mention ≥2 concrete tools/brands")

    return errors


def assert_moments(m: dict) -> list[str]:
    errors: list[str] = []
    moments = m.get("key_moments") or []
    if not moments:
        errors.append("key_moments empty")
        return errors

    blob = " ".join(
        f"{x.get('caption','')} {x.get('visible_text','')} {x.get('why','')}" for x in moments
    ).lower()
    if "не исчезала" in blob:
        errors.append("junk frame caption «НЕ ИСЧЕЗАЛА» present")

    stock_ok = any(s in blob for s in STOCK)
    if not stock_ok:
        errors.append("no moment caption/visible_text about stock brands")

    # Mismatch: quote step 2 vs caption step 4
    for x in moments:
        quote = _norm(str(x.get("quote") or ""))
        cap = _norm(f"{x.get('caption','')} {x.get('visible_text','')}")
        if "шаг 2" in quote and ("#4" in cap or ("музык" in cap and "шаг 2" not in cap)):
            errors.append(f"step mismatch quote=step2 caption={x.get('caption')!r}")

    # Captions should not all be empty
    with_cap = sum(1 for x in moments if (x.get("caption") or "").strip())
    if with_cap < max(1, len(moments) // 2):
        errors.append("too many empty moment captions")

    return errors


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=REF_ID, help="Material id to reprocess")
    parser.add_argument("--url", default=REF_URL, help="Fallback create URL if id missing")
    parser.add_argument("--skip-process", action="store_true", help="Only assert current DB state")
    args = parser.parse_args()

    from app.db import SessionLocal
    from app.models import Material
    from app.services.processing import process_material
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models import MaterialTag

    async with SessionLocal() as session:
        mid = args.id
        result = await session.execute(select(Material).where(Material.id == mid))
        material = result.scalars().first()
        if not material and args.url:
            from app.services.processing import create_material
            from app.services.sources import classify_url
            from app.services.queue import enqueue_material

            st = classify_url(args.url)
            material = await create_material(session, source_type=st, source_url=args.url)
            mid = material.id
            print(f"created material {mid}")
            if not args.skip_process:
                await enqueue_material(session, mid)
                # fall through to process directly for reliability
                material = await process_material(session, mid, force_full=True)
        elif not material:
            print(f"material {mid} not found", file=sys.stderr)
            return 2

        if not args.skip_process:
            print(f"reprocessing {mid} …")
            material = await process_material(session, mid, force_full=True)

        result = await session.execute(
            select(Material)
            .options(selectinload(Material.tags).selectinload(MaterialTag.tag))
            .where(Material.id == mid)
        )
        material = result.scalars().first()

    # Serialize like API
    m = {
        "id": material.id,
        "title": material.title,
        "digest": material.digest,
        "key_ideas": material.key_ideas or [],
        "tools": material.tools or [],
        "links": material.links or [],
        "raw_text": material.raw_text or "",
        "key_moments": material.key_moments or [],
        "usefulness_score": material.usefulness_score,
        "summary": material.summary,
        "main_idea": material.main_idea,
    }

    out = ROOT / "data" / "tmp_reel_probe" / "qa_sergienko_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    errors = assert_card(m) + assert_moments(m)
    print(json.dumps({
        "id": mid,
        "title": m["title"],
        "tools": m["tools"],
        "links": m["links"],
        "moments": [
            {"t": x.get("t_sec"), "caption": x.get("caption"), "quote": x.get("quote")}
            for x in (m["key_moments"] or [])
        ],
        "errors": errors,
        "ok": not errors,
    }, ensure_ascii=False, indent=2))

    if errors:
        print("FAILED", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
