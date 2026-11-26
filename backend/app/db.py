from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import PROJECT_ROOT, get_settings


class Base(DeclarativeBase):
    pass


def configure_sqlite_engine(target_engine) -> None:
    """Enable WAL, busy_timeout, foreign_keys, and NORMAL sync on every SQLite connection."""
    sync_engine = getattr(target_engine, "sync_engine", target_engine)

    @event.listens_for(sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _conn_record) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    kwargs: dict = {"pool_pre_ping": True}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        # Ensure parent dir exists for file DB
        if ":///" in url:
            raw = url.split(":///", 1)[1]
            if raw and raw != ":memory:":
                path = Path(raw)
                if not path.is_absolute():
                    path = PROJECT_ROOT / path
                path.parent.mkdir(parents=True, exist_ok=True)
                url = f"sqlite+aiosqlite:///{path.as_posix()}"
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    eng = create_async_engine(url, **kwargs)
    if is_sqlite:
        configure_sqlite_engine(eng)
    return eng


settings = get_settings()
engine = _make_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_lite_db() -> None:
    """Create tables + seed categories for SQLite lite mode."""
    from sqlalchemy import select

    from app.logging_setup import get_logger
    from app.models import Category
    from app.services.schema_migrate import ensure_schema
    from app.services.taxonomy import merge_duplicate_taxonomy

    log = get_logger(__name__)

    async with engine.begin() as conn:
        await ensure_schema(conn)

    async with SessionLocal() as session:
        existing = (await session.execute(select(Category).limit(1))).scalars().first()
        if not existing:
            for name in [
                "AI",
                "\u041c\u0430\u0440\u043a\u0435\u0442\u0438\u043d\u0433",  # Маркетинг
                "\u041f\u0440\u043e\u0434\u0430\u0436\u0438",  # Продажи
                "\u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435",  # Программирование
                "\u0414\u0438\u0437\u0430\u0439\u043d",  # Дизайн
                "\u0411\u0438\u0437\u043d\u0435\u0441",  # Бизнес
                "\u0424\u0440\u0438\u043b\u0430\u043d\u0441",  # Фриланс
            ]:
                session.add(Category(name=name))
            await session.commit()

    async with SessionLocal() as session:
        stats = await merge_duplicate_taxonomy(session)
        await session.commit()
        if any(stats.values()):
            log.info("taxonomy_startup_dedupe", **stats)
