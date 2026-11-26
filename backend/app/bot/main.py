from __future__ import annotations

import asyncio
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards import pending_tags_keyboard
from app.config import get_settings
from app.db import SessionLocal
from app.logging_setup import get_logger, setup_logging
from app.models import Material, MaterialTag, ProcessingStatus, Tag, TagStatus
from app.search import answer_question
from app.services.processing import create_material
from app.services.queue import enqueue_material
from app.services.sources import UnsupportedSourceError, classify_url
from app.services.storage import file_sha256, save_bytes, text_sha256, url_sha256

log = get_logger(__name__)

URL_RE = re.compile(r"https?://\S+", re.I)


def _is_owner_user(user_id: int | None) -> bool:
    settings = get_settings()
    if not settings.telegram_owner_id:
        if getattr(settings, "telegram_allow_public", False) or settings.allow_placeholder_secrets:
            return True
        return False
    return bool(user_id and user_id == settings.telegram_owner_id)


def _owner_ok(message: Message) -> bool:
    return _is_owner_user(message.from_user.id if message.from_user else None)


def _owner_cb(cb: CallbackQuery) -> bool:
    return _is_owner_user(cb.from_user.id if cb.from_user else None)


async def enqueue(material_id: str, force_full: bool = False) -> None:
    await enqueue_material(material_id, force_full)


def card_short(m: Material) -> str:
    return (
        f"{m.title or '—'}\n"
        f"score {m.usefulness_score}/10 — {m.usefulness_reason or ''}\n"
        f"{m.summary or ''}\n"
        f"id: `{m.id}`"
    )


async def _need_ai(message: Message) -> bool:
    """Return True if AI is ready; otherwise warn and return False."""
    settings = get_settings()
    if settings.ai_ready:
        return True
    await message.answer(
        "GROQ_API_KEY (или GOOGLE_AI_API_KEY) не задан в .env — "
        "обработка/поиск недоступны. Положи ключ и перезапусти API + бота."
    )
    return False


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        if not _owner_ok(message):
            await message.answer("Access denied. Укажи TELEGRAM_OWNER_ID своего user id.")
            return
        s = get_settings()
        await message.answer(
            "Chronicum MVP\n\n"
            "Пришли ссылку, файл, голосовое или текстовую заметку.\n"
            "Вопрос на естественном языке — поиск по базе.\n\n"
            "Команды:\n"
            "/whoami — твой Telegram id (для TELEGRAM_OWNER_ID)\n"
            "/status — готовность AI/токена\n"
            "/retry <id>\n"
            "/process_full <id>\n"
            "/score <id> <1-10>\n"
            "/approve_tag <tag>\n"
            "/reject_tag <tag>\n"
            "/pending_tags\n\n"
            f"AI: {'ok' if s.ai_ready else 'нет ключа'} · "
            f"owner_id: {s.telegram_owner_id or 'не задан (открытый доступ)'}"
        )

    @dp.message(Command("whoami"))
    async def whoami(message: Message) -> None:
        uid = message.from_user.id if message.from_user else "?"
        await message.answer(
            f"Твой Telegram user id: `{uid}`\n"
            f"Положи в .env:\nTELEGRAM_OWNER_ID={uid}"
        )

    @dp.message(Command("status"))
    async def status_cmd(message: Message) -> None:
        if not _owner_ok(message):
            return
        get_settings.cache_clear()
        s = get_settings()
        await message.answer(
            "Chronicum status\n"
            f"telegram_ready: {s.telegram_ready}\n"
            f"ai_ready: {s.ai_ready}\n"
            f"lite: {s.is_lite}\n"
            f"owner_id: {s.telegram_owner_id}\n"
            f"ai_provider: {s.ai_provider}"
        )

    @dp.message(Command("retry"))
    async def retry_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        if not await _need_ai(message):
            return
        mid = (command.args or "").strip()
        if not mid:
            await message.answer("Usage: /retry <id>")
            return
        async with SessionLocal() as session:
            m = (await session.execute(select(Material).where(Material.id == mid))).scalars().first()
            if not m:
                await message.answer("Not found")
                return
            m.processing_status = ProcessingStatus.QUEUED.value
            m.error_message = None
            await session.commit()
        await enqueue(mid)
        await message.answer(f"Re-queued `{mid}`")

    @dp.message(Command("process_full"))
    async def process_full_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        if not await _need_ai(message):
            return
        mid = (command.args or "").strip()
        if not mid:
            await message.answer("Usage: /process_full <id>")
            return
        await enqueue(mid, force_full=True)
        await message.answer(f"Full processing queued for `{mid}`")

    @dp.message(Command("score"))
    async def score_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        parts = (command.args or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Usage: /score <id> <1-10>")
            return
        mid, score = parts[0], int(parts[1])
        if score < 1 or score > 10:
            await message.answer("Score must be 1-10")
            return
        async with SessionLocal() as session:
            m = (await session.execute(select(Material).where(Material.id == mid))).scalars().first()
            if not m:
                await message.answer("Not found")
                return
            from datetime import datetime, timezone

            m.usefulness_score = score
            m.usefulness_reason = (m.usefulness_reason or "") + " [manual]"
            m.usefulness_updated_at = datetime.now(timezone.utc)
            await session.commit()
        await message.answer(f"Updated score for `{mid}` → {score}")

    @dp.message(Command("pending_tags"))
    async def pending_tags(message: Message) -> None:
        if not _owner_ok(message):
            return
        async with SessionLocal() as session:
            tags = (
                await session.execute(select(Tag).where(Tag.status == TagStatus.PENDING.value).limit(50))
            ).scalars().all()
        if not tags:
            await message.answer("Нет pending-тегов.")
            return
        pairs = [(t.id, t.name) for t in tags]
        kb = pending_tags_keyboard(pairs)
        await message.answer(
            "Pending теги (кнопки = approve / reject):\n"
            + "\n".join(f"• {t.name}" for t in tags),
            reply_markup=kb,
        )

    @dp.message(Command("approve_tag"))
    async def approve_tag(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        name = (command.args or "").strip()
        if not name:
            await message.answer("Usage: /approve_tag <name>")
            return
        async with SessionLocal() as session:
            tag = (await session.execute(select(Tag).where(Tag.name.ilike(name)))).scalars().first()
            if not tag:
                await message.answer("Tag not found")
                return
            tag.status = TagStatus.APPROVED.value
            links = (
                await session.execute(select(MaterialTag).where(MaterialTag.tag_id == tag.id))
            ).scalars().all()
            for link in links:
                link.proposed = False
            await session.commit()
            tag_name = tag.name
        await message.answer(f"Approved tag: {tag_name}")

    @dp.message(Command("reject_tag"))
    async def reject_tag_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        name = (command.args or "").strip()
        if not name:
            await message.answer("Usage: /reject_tag <name>")
            return
        async with SessionLocal() as session:
            tag = (await session.execute(select(Tag).where(Tag.name.ilike(name)))).scalars().first()
            if not tag:
                await message.answer("Tag not found")
                return
            tag.status = TagStatus.REJECTED.value
            links = (
                await session.execute(select(MaterialTag).where(MaterialTag.tag_id == tag.id))
            ).scalars().all()
            for link in links:
                await session.delete(link)
            await session.commit()
            tag_name = tag.name
        await message.answer(f"Rejected tag: {tag_name}")

    @dp.callback_query(F.data.startswith("pf:"))
    async def cb_process_full(cb: CallbackQuery) -> None:
        if not _owner_cb(cb):
            await cb.answer("Access denied", show_alert=True)
            return
        mid = (cb.data or "").split(":", 1)[-1]
        if not get_settings().ai_ready:
            await cb.answer("Нет AI-ключа", show_alert=True)
            return
        await enqueue(mid, force_full=True)
        await cb.answer("Full queued")
        if cb.message:
            await cb.message.answer(f"Полная обработка в очереди: `{mid}`")

    @dp.callback_query(F.data.startswith("rt:"))
    async def cb_retry(cb: CallbackQuery) -> None:
        if not _owner_cb(cb):
            await cb.answer("Access denied", show_alert=True)
            return
        mid = (cb.data or "").split(":", 1)[-1]
        if not get_settings().ai_ready:
            await cb.answer("Нет AI-ключа", show_alert=True)
            return
        async with SessionLocal() as session:
            m = (await session.execute(select(Material).where(Material.id == mid))).scalars().first()
            if not m:
                await cb.answer("Not found", show_alert=True)
                return
            m.processing_status = ProcessingStatus.QUEUED.value
            m.error_message = None
            await session.commit()
        await enqueue(mid)
        await cb.answer("Re-queued")

    @dp.callback_query(F.data.startswith("at:"))
    async def cb_approve_tag(cb: CallbackQuery) -> None:
        if not _owner_cb(cb):
            await cb.answer("Access denied", show_alert=True)
            return
        try:
            tag_id = int((cb.data or "").split(":", 1)[-1])
        except ValueError:
            await cb.answer("Bad id", show_alert=True)
            return
        async with SessionLocal() as session:
            tag = (await session.execute(select(Tag).where(Tag.id == tag_id))).scalars().first()
            if not tag:
                await cb.answer("Not found", show_alert=True)
                return
            tag.status = TagStatus.APPROVED.value
            links = (
                await session.execute(select(MaterialTag).where(MaterialTag.tag_id == tag_id))
            ).scalars().all()
            for link in links:
                link.proposed = False
            name = tag.name
            await session.commit()
        await cb.answer(f"Approved: {name}")

    @dp.callback_query(F.data.startswith("rj:"))
    async def cb_reject_tag(cb: CallbackQuery) -> None:
        if not _owner_cb(cb):
            await cb.answer("Access denied", show_alert=True)
            return
        try:
            tag_id = int((cb.data or "").split(":", 1)[-1])
        except ValueError:
            await cb.answer("Bad id", show_alert=True)
            return
        async with SessionLocal() as session:
            tag = (await session.execute(select(Tag).where(Tag.id == tag_id))).scalars().first()
            if not tag:
                await cb.answer("Not found", show_alert=True)
                return
            tag.status = TagStatus.REJECTED.value
            links = (
                await session.execute(select(MaterialTag).where(MaterialTag.tag_id == tag_id))
            ).scalars().all()
            for link in links:
                await session.delete(link)
            name = tag.name
            await session.commit()
        await cb.answer(f"Rejected: {name}")

    @dp.message(F.voice | F.audio)
    async def on_voice(message: Message, bot: Bot) -> None:
        if not _owner_ok(message):
            return
        if not await _need_ai(message):
            return
        file = message.voice or message.audio
        tg_file = await bot.get_file(file.file_id)
        data = await bot.download_file(tg_file.file_path)
        raw = data.read() if hasattr(data, "read") else data
        path = save_bytes(raw, ".ogg")
        content_hash = file_sha256(path)
        async with SessionLocal() as session:
            m = await create_material(
                session,
                source_type="voice",
                file_path=path,
                content_hash=content_hash,
                user_id=message.from_user.id if message.from_user else None,
                chat_id=message.chat.id,
            )
        if m.processing_status == ProcessingStatus.DONE.value:
            await message.answer(f"Already exists:\n{card_short(m)}")
            return
        await enqueue(m.id)
        await message.answer(f"Принято (voice), в очереди.\nid: `{m.id}`")

    @dp.message(F.photo | F.document)
    async def on_file(message: Message, bot: Bot) -> None:
        if not _owner_ok(message):
            return
        if not await _need_ai(message):
            return

        if message.photo:
            photo = message.photo[-1]
            tg_file = await bot.get_file(photo.file_id)
            data = await bot.download_file(tg_file.file_path)
            raw = data.read() if hasattr(data, "read") else data
            path = save_bytes(raw, ".jpg")
            source_type = "image"
        else:
            doc = message.document
            assert doc
            name = (doc.file_name or "file").lower()
            tg_file = await bot.get_file(doc.file_id)
            data = await bot.download_file(tg_file.file_path)
            raw = data.read() if hasattr(data, "read") else data
            suffix = Path(name).suffix or ""
            path = save_bytes(raw, suffix)
            if suffix in {".pdf"} or "pdf" in (doc.mime_type or ""):
                source_type = "pdf"
            elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} or (doc.mime_type or "").startswith(
                "image/"
            ):
                source_type = "image"
            elif suffix in {".mp4", ".mov", ".mkv", ".webm"} or (doc.mime_type or "").startswith("video/"):
                source_type = "video"
            else:
                await message.answer(f"Unsupported file type: {name}")
                return

        content_hash = file_sha256(path)
        async with SessionLocal() as session:
            m = await create_material(
                session,
                source_type=source_type,
                file_path=path,
                content_hash=content_hash,
                user_id=message.from_user.id if message.from_user else None,
                chat_id=message.chat.id,
            )
        if m.processing_status == ProcessingStatus.DONE.value and m.summary:
            await message.answer(f"Already exists:\n{card_short(m)}")
            return
        await enqueue(m.id)
        await message.answer(f"Принято ({source_type}), в очереди.\nid: `{m.id}`")

    @dp.message(F.video | F.video_note)
    async def on_video(message: Message, bot: Bot) -> None:
        if not _owner_ok(message):
            return
        if not await _need_ai(message):
            return
        video = message.video or message.video_note
        tg_file = await bot.get_file(video.file_id)
        data = await bot.download_file(tg_file.file_path)
        raw = data.read() if hasattr(data, "read") else data
        path = save_bytes(raw, ".mp4")
        content_hash = file_sha256(path)
        async with SessionLocal() as session:
            m = await create_material(
                session,
                source_type="video",
                file_path=path,
                content_hash=content_hash,
                user_id=message.from_user.id if message.from_user else None,
                chat_id=message.chat.id,
            )
        if m.summary and m.processing_status == ProcessingStatus.DONE.value:
            await message.answer(f"Already exists:\n{card_short(m)}")
            return
        await enqueue(m.id)
        await message.answer(f"Принято (video), в очереди.\nid: `{m.id}`")

    @dp.message(F.text)
    async def on_text(message: Message) -> None:
        if not _owner_ok(message):
            return
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return

        is_question = text.endswith("?") or text.lower().startswith(
            ("как ", "что ", "зачем ", "почему ", "where ", "what ", "how ", "найди", "search")
        )

        url_match = URL_RE.search(text)
        mostly_url = bool(url_match) and (
            text.strip() == url_match.group(0) or (len(text) < 400 and not is_question)
        )
        if mostly_url and url_match:
            if not await _need_ai(message):
                return
            url = url_match.group(0).rstrip(").,]")
            try:
                source_type = classify_url(url)
            except UnsupportedSourceError as e:
                await message.answer(str(e))
                return
            content_hash = url_sha256(url)
            async with SessionLocal() as session:
                m = await create_material(
                    session,
                    source_type=source_type,
                    source_url=url,
                    content_hash=content_hash,
                    user_id=message.from_user.id if message.from_user else None,
                    chat_id=message.chat.id,
                )
            if m.summary and m.processing_status == ProcessingStatus.DONE.value:
                await message.answer(f"Already exists:\n{card_short(m)}")
                return
            await enqueue(m.id)
            await message.answer(f"Принято ({source_type}), в очереди.\nid: `{m.id}`")
            return

        if is_question or len(text) > 280:
            if not await _need_ai(message):
                return
            async with SessionLocal() as session:
                answer, _ = await answer_question(session, text)
            await message.answer(answer[:4000])
            return

        if not await _need_ai(message):
            return
        content_hash = text_sha256(text)
        async with SessionLocal() as session:
            m = await create_material(
                session,
                source_type="note",
                note_text=text,
                content_hash=content_hash,
                user_id=message.from_user.id if message.from_user else None,
                chat_id=message.chat.id,
                title=text[:80],
            )
        if m.summary and m.processing_status == ProcessingStatus.DONE.value:
            await message.answer(f"Already exists:\n{card_short(m)}")
            return
        await enqueue(m.id)
        await message.answer(f"Принято (note), в очереди.\nid: `{m.id}`")

    return dp


async def run_bot() -> None:
    setup_logging(get_settings().log_level)
    get_settings.cache_clear()
    settings = get_settings()

    if not settings.telegram_ready:
        log.warning(
            "bot_waiting_for_telegram_token",
            hint="Put TELEGRAM_BOT_TOKEN (+ TELEGRAM_OWNER_ID) into .env; bot will pick it up.",
        )
        print(
            "\n[Chronicum bot] TELEGRAM_BOT_TOKEN ещё placeholder.\n"
            "1) Создай бота у @BotFather\n"
            "2) Положи TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID в .env\n"
            "   (/whoami в боте после старта, или узнай id через @userinfobot)\n"
            "3) Процесс ждёт… или перезапусти после правки .env\n"
        )
        while True:
            await asyncio.sleep(15)
            get_settings.cache_clear()
            settings = get_settings()
            if settings.telegram_ready:
                log.info("telegram_token_detected")
                break
            log.info("still_waiting_for_TELEGRAM_BOT_TOKEN")

    if not settings.ai_ready:
        log.warning(
            "bot_starting_without_ai",
            hint="Processing will fail until GROQ_API_KEY is set; polling still starts.",
        )
        print("[Chronicum bot] WARNING: AI key missing — intake messages will warn until Groq is set.\n")

    settings.require_telegram()
    bot = Bot(token=settings.telegram_bot_token)
    dp = create_dispatcher()
    me = await bot.get_me()
    log.info("bot_starting", username=me.username, owner_id=settings.telegram_owner_id)
    print(f"[Chronicum bot] @{me.username} polling… owner_id={settings.telegram_owner_id}")
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
