from __future__ import annotations

import asyncio
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
from app.services.processing import create_material, needs_enqueue
from app.services.queue import enqueue_material
from app.services.storage import file_sha256, save_bytes, text_sha256

log = get_logger(__name__)


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


async def _ai_is_ready() -> bool:
    """True if AI key is configured in .env OR stored in the DB key pool."""
    settings = get_settings()
    if settings.ai_ready:
        return True
    try:
        from app.services.key_pool import any_provider_ready

        async with SessionLocal() as session:
            return await any_provider_ready(session)
    except Exception:
        return False


async def _need_ai(message: Message) -> bool:
    """Return True if AI is ready; otherwise warn and return False."""
    if await _ai_is_ready():
        return True
    await message.answer(
        "Не найдено ни одного активного AI-ключа (ни в .env, ни в Настройках Web). "
        "Добавь Groq или Gemini ключ в Настройках или в .env."
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
        ai_ok = await _ai_is_ready()
        await message.answer(
            "Chronicum MVP\n\n"
            "Пришли ссылку, файл, голосовое или текстовую заметку.\n"
            "Вопрос на естественном языке (или /ask <вопрос>) — поиск по базе.\n\n"
            "Команды:\n"
            "/ask <вопрос> — поиск по базе\n"
            "/whoami — твой Telegram id (для TELEGRAM_OWNER_ID)\n"
            "/status — готовность AI/токена\n"
            "/retry <id>\n"
            "/process_full <id>\n"
            "/score <id> <1-10>\n"
            "/approve_tag <tag>\n"
            "/reject_tag <tag>\n"
            "/pending_tags\n"
            "/client <имя> <текст> — дописать в тред клиента\n"
            "/client_context <имя> — сводка по клиенту\n"
            "/client_new <имя> — создать карточку\n\n"
            f"AI: {'ok' if ai_ok else 'нет ключа'} · "
            f"owner_id: {s.telegram_owner_id or 'не задан (открытый доступ)'}"
        )

    @dp.message(Command("ask"))
    async def ask_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        q = (command.args or "").strip()
        if not q:
            await message.answer("Usage: /ask <вопрос по базе>")
            return
        if not await _need_ai(message):
            return
        async with SessionLocal() as session:
            answer, _ = await answer_question(session, q)
        await message.answer(answer[:4000])

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
        ai_ok = await _ai_is_ready()
        usage = ""
        try:
            from datetime import datetime, timezone

            from sqlalchemy import func, select

            from app.models import Material

            soft_limit = 250_000
            day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            async with SessionLocal() as session:
                row = (
                    await session.execute(
                        select(
                            func.coalesce(func.sum(Material.usage_tokens_in), 0),
                            func.coalesce(func.sum(Material.usage_tokens_out), 0),
                        ).where(Material.updated_at >= day_start)
                    )
                ).one()
                used = int(row[0] or 0) + int(row[1] or 0)
            lvl = "warn" if used >= soft_limit else "ok"
            usage = f"\ngroq_tokens_today: {used} ({lvl}, soft={soft_limit})"
        except Exception:
            usage = ""
        await message.answer(
            "Chronicum status\n"
            f"telegram_ready: {s.telegram_ready}\n"
            f"ai_ready: {ai_ok}\n"
            f"lite: {s.is_lite}\n"
            f"owner_id: {s.telegram_owner_id}\n"
            f"ai_provider: {s.ai_provider}"
            f"{usage}"
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

    @dp.message(Command("client_new"))
    async def client_new_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        name = (command.args or "").strip()
        if not name:
            await message.answer("Usage: /client_new <имя>")
            return
        from app.services.clients import create_client

        async with SessionLocal() as session:
            c = await create_client(session, display_name=name)
        await message.answer(f"Клиент создан: {c.display_name}\nid: `{c.id}`")

    @dp.message(Command("client"))
    async def client_append_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        raw = (command.args or "").strip()
        if not raw:
            await message.answer("Usage: /client <имя|id> <текст сообщения>")
            return
        from app.services.clients import AmbiguousClientError, append_message, resolve_client_v2

        async with SessionLocal() as session:
            # Longest-prefix resolve so multi-word names work: «Иван Петров текст…»
            chosen = None
            chosen_text = ""
            words = raw.split()
            for cut in range(len(words) - 1, 0, -1):
                who_try = " ".join(words[:cut])
                res = await resolve_client_v2(session, who_try)
                if res["status"] == "ambiguous":
                    names = "\n".join(
                        f"• {c['display_name']} ({c['id'][:8]}…) — {c.get('context_line') or ''}"
                        for c in res["candidates"]
                    )
                    await message.answer(f"Неоднозначно, уточни id:\n{names}")
                    return
                if res["client"]:
                    chosen = res["client"]
                    chosen_text = " ".join(words[cut:])
                    break
            if not chosen or not chosen_text:
                await message.answer("Клиент не найден или нет текста. Usage: /client <имя|id> <текст>")
                return
            c, _msg = await append_message(session, chosen.id, chosen_text, source="tg", refresh_summary=False)
        await message.answer(f"Дописано → {c.display_name} ({c.id[:8]}…)")

    @dp.message(Command("client_summary"))
    async def client_summary_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        who = (command.args or "").strip()
        if not who:
            await message.answer("Usage: /client_summary <имя|id>")
            return
        from app.services.clients import AmbiguousClientError, refresh_client_summary, resolve_client

        async with SessionLocal() as session:
            try:
                c0 = await resolve_client(session, who)
            except AmbiguousClientError as e:
                names = "\n".join(f"• {c['display_name']} ({c['id'][:8]}…)" for c in e.candidates)
                await message.answer(f"Неоднозначно:\n{names}")
                return
            if not c0:
                await message.answer(f"Клиент не найден: {who}")
                return
            c = await refresh_client_summary(session, c0.id)
        await message.answer(f"Сводка обновлена (v{c.summary_version}): {c.display_name}")

    @dp.message(Command("client_context"))
    async def client_context_cmd(message: Message, command: CommandObject) -> None:
        if not _owner_ok(message):
            return
        who = (command.args or "").strip()
        if not who:
            await message.answer("Usage: /client_context <имя|id>")
            return
        from app.services.clients import AmbiguousClientError, client_context_pack

        async with SessionLocal() as session:
            try:
                pack = await client_context_pack(session, who, n=8)
            except AmbiguousClientError as e:
                names = "\n".join(f"• {c['display_name']} ({c['id'][:8]}…)" for c in e.candidates)
                await message.answer(f"Неоднозначно:\n{names}")
                return
            except ValueError:
                await message.answer(f"Клиент не найден: {who}")
                return
        cl = pack["client"]
        stale_note = ""
        if cl.get("summary_stale"):
            stale_note = f"\n(сводка устарела: +{cl.get('messages_since_summary')} новых сообщений)"
        summary = (cl.get("summary") or "(сводки ещё нет — /client … затем обнови в вебе)").strip()
        recent = pack.get("recent_messages") or []
        tail = "\n".join(f"• {(m.get('body') or '')[:120]}" for m in recent[-5:])
        await message.answer(
            f"{cl.get('display_name')} ({cl.get('id')}){stale_note}\n\n{summary[:2500]}\n\nПоследние:\n{tail or '—'}"
        )

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
            out = await create_material(
                session,
                source_type="voice",
                file_path=path,
                content_hash=content_hash,
                user_id=message.from_user.id if message.from_user else None,
                chat_id=message.chat.id,
            )
            m = out.material
            if out.already_exists and m.processing_status == ProcessingStatus.FAILED.value:
                m.processing_status = ProcessingStatus.QUEUED.value
                m.error_message = None
                await session.commit()
        if out.already_exists and not needs_enqueue(m, out.already_exists):
            await message.answer(f"Already exists:\n{card_short(m)}")
            return
        if needs_enqueue(m, out.already_exists):
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
            out = await create_material(
                session,
                source_type=source_type,
                file_path=path,
                content_hash=content_hash,
                user_id=message.from_user.id if message.from_user else None,
                chat_id=message.chat.id,
            )
            m = out.material
            if out.already_exists and m.processing_status == ProcessingStatus.FAILED.value:
                m.processing_status = ProcessingStatus.QUEUED.value
                m.error_message = None
                await session.commit()
        if out.already_exists and not needs_enqueue(m, out.already_exists):
            await message.answer(f"Already exists:\n{card_short(m)}")
            return
        if needs_enqueue(m, out.already_exists):
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
            out = await create_material(
                session,
                source_type="video",
                file_path=path,
                content_hash=content_hash,
                user_id=message.from_user.id if message.from_user else None,
                chat_id=message.chat.id,
            )
            m = out.material
            if out.already_exists and m.processing_status == ProcessingStatus.FAILED.value:
                m.processing_status = ProcessingStatus.QUEUED.value
                m.error_message = None
                await session.commit()
        if out.already_exists and not needs_enqueue(m, out.already_exists):
            await message.answer(f"Already exists:\n{card_short(m)}")
            return
        if needs_enqueue(m, out.already_exists):
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

        from app.services.composer import extract_urls, run_composer, _url_paste_only

        urls = extract_urls(text)
        if urls and _url_paste_only(text, urls) and not is_question:
            if not await _need_ai(message):
                return
            try:
                async with SessionLocal() as session:
                    result = await run_composer(session, text, mode="knowledge")
            except ValueError as e:
                await message.answer(str(e))
                return
            lines = [result.message]
            for it in result.items or []:
                mark = "·" if it.get("already_exists") else "+"
                lines.append(f"{mark} `{it['id']}`")
            await message.answer("\n".join(lines)[:4000])
            return

        if is_question:
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
            out = await create_material(
                session,
                source_type="note",
                note_text=text,
                content_hash=content_hash,
                user_id=message.from_user.id if message.from_user else None,
                chat_id=message.chat.id,
                title=text[:80],
            )
            m = getattr(out, "material", out)
            already = bool(getattr(out, "already_exists", False))
            if already and getattr(m, "processing_status", None) == ProcessingStatus.FAILED.value:
                m.processing_status = ProcessingStatus.QUEUED.value
                m.processing_stage = None
                m.error_message = None
                await session.commit()
        if already and not needs_enqueue(m, already):
            await message.answer(f"Already exists:\n{card_short(m)}")
            return
        if not already or needs_enqueue(m, already):
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

    settings.validate_security_posture()
    for warning in settings.security_warnings():
        log.warning("security_posture_warning", detail=warning)

    if settings.is_lite:
        from app.db import init_lite_db
        from app.services.queue import recover_lite_queue

        await init_lite_db()
        await recover_lite_queue()

    if not await _ai_is_ready():
        log.warning(
            "bot_starting_without_ai",
            hint="Processing will fail until GROQ_API_KEY/Google key is set in .env or Web Settings.",
        )
        print("[Chronicum bot] WARNING: AI key missing — intake messages will warn until a key is configured.\n")

    settings.require_telegram()
    bot = Bot(token=settings.telegram_bot_token)
    dp = create_dispatcher()
    me = await bot.get_me()
    log.info("bot_starting", username=me.username, owner_id=settings.telegram_owner_id)
    print(f"[Chronicum bot] @{me.username} polling… owner_id={settings.telegram_owner_id}")
    try:
        await dp.start_polling(bot)
    finally:
        if settings.is_lite:
            from app.services.queue import shutdown_lite_queue

            await shutdown_lite_queue()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
