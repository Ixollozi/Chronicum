#!/usr/bin/env python3
"""Reprocess githubradar reel and assert listicle quality gates (P1)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

REF_URL = "https://www.instagram.com/githubradar/reel/DdOPmR8Au0j/"
REF_ID = "e5a2b7d8-cd85-4363-afd3-0f5b1b04b74e"

BRANDS = ("rybbit", "witr", "boardui")
SLUGS = (
    "github.com/rybbit-io/rybbit",
    "github.com/boardui/boardui",
    "github.com/pranshuparmar/witr",
)


def _blob(m: dict) -> str:
    parts = [
        m.get("title") or "",
        m.get("digest") or "",
        " ".join(m.get("tools") or []),
        " ".join(str(x.get("url") or "") for x in (m.get("links") or []) if isinstance(x, dict)),
        " ".join(str(x) for x in (m.get("links") or []) if isinstance(x, str)),
    ]
    return " ".join(parts).lower()


def assert_card(m: dict) -> list[str]:
    errors: list[str] = []
    blob = _blob(m)
    tools_blob = " ".join(m.get("tools") or []).lower()

    if "rybit" in tools_blob and "rybbit" not in tools_blob:
        errors.append("tools still has ASR glitch Rybit without Rybbit")
    if "vitter" in tools_blob:
        errors.append("tools still has ASR glitch Vitter")
    if "rybbit" not in blob:
        errors.append("missing Rybbit brand in title/tools/digest/links")
    if "witr" not in blob:
        errors.append("missing witr brand in title/tools/digest/links")

    gh_hits = sum(1 for s in SLUGS if s in blob)
    # Accept any ≥2 distinct github.com/owner/repo style links
    link_urls = []
    for x in m.get("links") or []:
        if isinstance(x, dict):
            link_urls.append(str(x.get("url") or "").lower())
        else:
            link_urls.append(str(x).lower())
    slug_links = [u for u in link_urls if "github.com/" in u and u.count("/") >= 4]
    if len(slug_links) < 2 and gh_hits < 2:
        errors.append(f"need ≥2 github owner/repo links, got {slug_links!r}")

    score = m.get("usefulness_score")
    if score is not None and int(score) > 7:
        errors.append(f"promo listicle score must be ≤7, got {score}")

    return errors


def assert_moments(m: dict) -> list[str]:
    errors: list[str] = []
    moments = m.get("key_moments") or []
    if not moments:
        errors.append("key_moments empty")
        return errors

    blob = " ".join(
        f"{x.get('caption','')} {x.get('visible_text','')} {x.get('why','')} {x.get('_ocr','')}"
        for x in moments
    ).lower()

    # Percent-only junk should not dominate moments (caption is source of truth)
    pct_only = 0
    for x in moments:
        cap = f"{x.get('caption','')}".lower()
        if cap.count("%") >= 3 and "github" not in cap and not re.search(r"[a-z]{3,}", cap):
            pct_only += 1
    if pct_only:
        errors.append(f"{pct_only} percent-heavy junk moment(s)")


    if "подпис" in blob and "rybbit" not in blob and "witr" not in blob:
        errors.append("CTA-only moments without project brands")

    brand_ok = any(b in blob for b in BRANDS) or any("/" in (x.get("caption") or "") for x in moments)
    if not brand_ok:
        errors.append("no moment caption about project brands/slugs")

    return errors


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=REF_ID, help="Material id to reprocess")
    parser.add_argument("--url", default=REF_URL, help="Fallback create URL if id missing")
    parser.add_argument("--skip-process", action="store_true", help="Only assert current DB state")
    args = parser.parse_args()

    from app.db import SessionLocal
    from app.models import Material, MaterialTag
    from app.services.processing import process_material
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with SessionLocal() as session:
        mid = args.id
        result = await session.execute(select(Material).where(Material.id == mid))
        material = result.scalars().first()
        if not material and args.url:
            from app.services.processing import create_material
            from app.services.sources import classify_url

            st = classify_url(args.url)
            material = await create_material(session, source_type=st, source_url=args.url)
            mid = material.id
            print(f"created material {mid}")
            if not args.skip_process:
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

    out = ROOT / "data" / "qa-githubradar" / "qa_githubradar_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

    errors = assert_card(m) + assert_moments(m)
    print(
        json.dumps(
            {
                "id": mid,
                "title": m["title"],
                "score": m["usefulness_score"],
                "tools": m["tools"],
                "links": m["links"],
                "moments": [
                    {"t": x.get("t_sec"), "caption": x.get("caption"), "quote": x.get("quote")}
                    for x in (m["key_moments"] or [])
                ],
                "errors": errors,
                "ok": not errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if errors:
        print("FAILED", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
