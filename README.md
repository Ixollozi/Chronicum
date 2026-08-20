# Chronicum MVP

Персональная база знаний: **Telegram + веб-кабинет** → очередь → пайплайны → AI-карточка → FTS-поиск.

Стек: FastAPI + Next.js + PostgreSQL + Redis/ARQ + Groq.  
Post-MVP: [ROADMAP_POST_MVP.md](ROADMAP_POST_MVP.md). Лимиты: [GROQ_LIMITS_PREFLIGHT.md](GROQ_LIMITS_PREFLIGHT.md).

## Интерфейсы

| Канал | Роль |
|---|---|
| **Веб** http://127.0.0.1:3000 | Каталог, карточка, загрузка, NL-поиск |
| **Telegram-бот** | Быстрый intake |
| **MCP** [mcp/README.md](mcp/README.md) | Cursor / Claude / HTTP — нейронки ходят в базу, а не в папки проекта |

## Запуск без Docker — LITE (рекомендуется)

SQLite + очередь в процессе API. Не нужны Postgres/Redis/Docker.

```powershell
cd D:\Cursor\Chronicum
# пропиши GROQ_API_KEY в .env
.\scripts\start-lite.ps1
```

Откроется http://127.0.0.1:3000 (токен `chronicum-dev-token`).

Full-режим (Postgres+Redis): `.\scripts\setup-local.ps1` + `.\scripts\start-local.ps1`.

## Запуск через Docker (опционально)

В `.env` раскомментируй docker-hostnames (`@postgres`, `@redis`, `STORAGE_PATH=/data/files`), затем:

```bash
docker compose up --build -d
```

## Веб-страницы

- `/` — спросить базу / сохранить ссылку или заметку
- `/archive` — каталог
- `/graph` — сеть тегов и категорий
- `/m/[id]` — карточка
- `/settings` — режимы, профиль, ключи, **подключение MCP к Cursor**

## Bot commands

| Команда | Действие |
|---|---|
| ссылка / файл / voice / текст | В очередь |
| вопрос | NL-поиск |
| `/retry <id>` | Повтор |
| `/process_full <id>` | Full для длинного видео |
| `/score <id> <1-10>` | Оценка |
| `/pending_tags` / `/approve_tag` | Теги |

## Video

По умолчанию Whisper расшифровывает **ролик целиком** (Настройки → Режимы → «Видео целиком»). Выключи тоггл, если нужен старый preview ~20% + full по кнопке.

## Backup

Lite (SQLite):

```powershell
.\scripts\backup-lite.ps1
```

Postgres / Docker:

```bash
./scripts/backup_pg.sh
```

Для локального Postgres без Docker выставь `DATABASE_URL` — скрипт уйдёт в `pg_dump`.
