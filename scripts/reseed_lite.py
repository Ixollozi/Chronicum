import asyncio
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ["CHRONICUM_LITE"] = "true"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(ROOT / 'data' / 'chronicum.db').as_posix()}"

db_path = ROOT / "data" / "chronicum.db"
if db_path.exists():
    db_path.unlink()

from app.config import get_settings

get_settings.cache_clear()

from app.db import init_lite_db


async def main():
    await init_lite_db()
    import sqlite3

    c = sqlite3.connect(db_path)
    rows = c.execute("select id, name from categories").fetchall()
    out = ROOT / "logs" / "categories_utf8.txt"
    out.write_text("\n".join(f"{i}:{n}" for i, n in rows), encoding="utf-8")
    print("ok", rows)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    asyncio.run(main())
