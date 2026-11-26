# Chronicum

Персональная база знаний: **Telegram + веб-кабинет** → очередь → пайплайны → AI-карточка → поиск с русской морфологией и NL-ответами.

Стек: FastAPI + Next.js + SQLite (Lite) / PostgreSQL + Redis/ARQ (Full) + Groq / Gemini.  
Post-MVP: [ROADMAP_POST_MVP.md](ROADMAP_POST_MVP.md). Лимиты AI: [GROQ_LIMITS_PREFLIGHT.md](GROQ_LIMITS_PREFLIGHT.md).

## Интерфейсы

| Канал | Роль |
|---|---|
| **Веб** http://127.0.0.1:3000 | Каталог, карточка, загрузка, NL-поиск, граф, модерация тегов, пул ключей |
| **Telegram-бот** | Быстрый intake ссылок, файлов, голосовых и заметок |
| **MCP** [mcp/README.md](mcp/README.md) | Cursor / Claude / VS Code / HTTP — нейронки ходят в базу, а не в папки проекта |

## Запуск без Docker — LITE (рекомендуется)

SQLite + очередь в процессе API с авто-восстановлением незавершённых задач (`queued`/`processing`) после перезапуска. Не нужны Postgres/Redis/Docker.

### Windows (PowerShell)

```powershell
cp .env.example .env
# пропиши GROQ_API_KEY (и при желании GOOGLE_AI_API_KEY) в .env
.\scripts\start-lite.ps1
```

### Linux / macOS

```bash
cp .env.example .env
# пропиши GROQ_API_KEY (и при желании GOOGLE_AI_API_KEY) в .env

# Backend API (+ встроенный HTTP MCP на /mcp)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000

# В отдельном терминале — Frontend
cd web && npm install && npm run dev
```

Откроется http://127.0.0.1:3000 (токен по умолчанию `chronicum-dev-token` — обязательно смени `WEB_API_TOKEN` и `NEXT_PUBLIC_WEB_API_TOKEN`, если открываешь доступ вне `localhost`).

Full-режим (Postgres + Redis + ARQ): `.\scripts\setup-local.ps1` + `.\scripts\start-local.ps1` (или `CHRONICUM_LITE=false` с `DATABASE_URL` на PostgreSQL и запущенным `arq app.workers.settings.WorkerSettings`).

## Запуск через Docker (опционально)

В `.env` укажи docker-hostnames (`@postgres`, `@redis`, `STORAGE_PATH=/data/files`), затем:

```bash
docker compose up --build -d
```

## Веб-страницы

- `/` — спросить базу / сохранить ссылку, задачу, правило или заметку
- `/archive` — каталог с фильтрами и поиском (учитывает словоформы русского и английского языков)
- `/graph` — сеть материалов, тегов и категорий
- `/m/[id]` — карточка материала (выжимка, объяснение, оценка, ключевые моменты)
- `/settings` — режимы разбора, личный контекст, пул API-ключей (Groq + Gemini), **модерация предложенных тегов**, подключение MCP к IDE

## Bot commands

| Команда | Действие |
|---|---|
| ссылка / файл / voice / текст | В очередь |
| вопрос | NL-поиск (использует пул ключей Groq → Gemini) |
| `/retry <id>` | Повтор обработки |
| `/process_full <id>` | Полный разбор длинного видео |
| `/score <id> <1-10>` | Ручная оценка полезности |
| `/pending_tags` / `/approve_tag` | Модерация новых тегов (также доступно в Веб → Настройки → Теги) |

## Видео, субтитры и Whisper

- **YouTube captions first-pass (`YOUTUBE_PREFER_CAPTIONS=true`):** для YouTube сначала забираются ручные или автоматические субтитры без скачивания видео и без расхода квоты Whisper. Если субтитров нет или они слишком короткие — автоматически включается fallback на скачивание + Whisper.
- **Чанкинг длинного аудио (`WHISPER_MAX_FILE_MB=20`, `WHISPER_CHUNK_SECONDS=600`):** аудио тяжелее 20 МБ автоматически режется через `ffmpeg` на части с учётом лимита Groq (25 МБ).
- **Режим превью:** по умолчанию обрабатывается **ролик целиком** (Настройки → Режимы → «Видео целиком»). Если выключить тоггл, длинные видео (> `VIDEO_PREVIEW_THRESHOLD_MIN`, по умолчанию 10 мин) сначала разбираются в preview-режиме (~`VIDEO_PREVIEW_FRACTION`, 20%) с авто-апгрейдом при высокой оценке или по кнопке `/process_full`.
- **Instagram / TikTok cookies:** при rate-limit укажи `MEDIA_COOKIES_FILE` (Netscape `cookies.txt`) или `MEDIA_COOKIES_FROM_BROWSER=chrome`.

## Модерация тегов

Новые теги, предложенные ИИ при разборе материала, создаются в статусе `pending` (в карточке помечаются знаком `?`) и не засоряют словарь автоматически. Одобрить или отклонить их можно в **Настройки → Теги** или через `/pending_tags` в Telegram.

## Ключевые переменные `.env`

См. полный список с комментариями в [`.env.example`](.env.example). Основные группы:

- AI и транскрибация: `GROQ_API_KEY`, `GOOGLE_AI_API_KEY`, `GROQ_LLM_MODEL`, `GROQ_WHISPER_MODEL`, `WHISPER_LANGUAGE`, `WHISPER_MAX_FILE_MB`, `WHISPER_CHUNK_SECONDS`, `YOUTUBE_PREFER_CAPTIONS`.
- Медиа и OCR: `MEDIA_COOKIES_FILE`, `MEDIA_COOKIES_FROM_BROWSER`, `TESSERACT_CMD`, `VIDEO_CONCURRENCY`.
- Безопасность: `WEB_API_TOKEN`, `NEXT_PUBLIC_WEB_API_TOKEN`, `CORS_ORIGINS`, `ALLOW_PLACEHOLDER_SECRETS=false` для не-dev окружений.
- MCP stdio-клиент: `CHRONICUM_API_URL`, `CHRONICUM_TOKEN`.

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
