# Chronicum — за пределами MVP

Документ фиксирует то, что **не входит** в Фазу 1 (MVP), но следует из [TZ_AI_Knowledge_Base.md](TZ_AI_Knowledge_Base.md) и продуктовых улучшений сверх ТЗ.

**MVP (Фаза 1)** реализуется отдельно по утверждённому плану. В MVP уже входят: Telegram core loop, **веб-кабинет** (`web/`), пайплайны источников из ТЗ, управляемая таксономия, FTS-поиск с источниками, **двухступенчатая обработка видео (~20% → full)** — сюда их не переносим.


Источники: ТЗ §2–3 (границы MVP), §3.3 «вне MVP», §3.10 веб-UI, §4 (pgvector не в MVP), §6–8 (фазы 2–4).

---

## 1. Вне MVP, но рядом (можно сразу после стабильного core loop)

| Что | Статус | Детали |
|---|---|---|
| Автоскрейп Instagram Reels / TikTok по ссылке | **Сделано** | yt-dlp + fallback-парсеры каруселей + управление Netscape `cookies.txt` прямо в Веб → Настройки → Режимы → Cookies; класс A = upload + `source_url` |
| Правда Reels-listicle (ASR/OCR/score/moments) | QA githubradar — **P0/P1 закрыты** | Grounding/evidence verification, talking-head / пустой ASR, moments auto-gate (`auto_moments=false`) — [QA](QA_INSTAGRAM_LISTICLE_FINDINGS.md), [SYSTEM_AUDIT §10](SYSTEM_AUDIT.md) |
| Простой веб-UI (список карточек, фильтры, просмотр) | **Сделано** | Кабинет Next.js (`web/`): расширенные фильтры архива (источник, тип, score, тег, сортировка), клиенты (`/clients`), модерация pending-тегов, Google OAuth + PAT, PWA (`site.webmanifest` + `/sw.js`) |
| Лёгкий embedding-поиск (hybrid semantic + FTS) | **Сделано** | Локальные эмбеддинги (`local-hash-v1`) + опциональный Gemini `text-embedding-004`, гибридное ранжирование в SQLite и Postgres |
| «Доверительный режим» авто-подтверждения тегов | В бэклоге | ТЗ §3.6 — снизить трение после недели ручных approve |
| Еженедельный авто-дедуп похожих тегов | Частично | Есть ручной/базовый merge дублей; авто-cron — позже |
| Пересчёт usefulness в контексте проектов | В бэклоге | ТЗ §3.7 (Фаза 2+) — имеет смысл только с проектами |
| S3-совместимое хранилище вместо FS VPS | В бэклоге | ТЗ §4 (абстракция FS уже выделена) |

---

## 2. Фаза 2 — Personal Agency OS

Активировать **только** после DoD MVP + 2 недели ежедневного использования.

- Управление проектами: идея → AI оформляет → проект → генерация ТЗ → задачи.
- Автопривязка релевантных материалов из базы знаний к задачам/проектам.
- Управление клиентами.
- Полноценный веб-интерфейс (вместо или вместе с Telegram-ботом).

---

## 3. Фаза 3 — Semantic Search и Knowledge Graph

- [x] Лёгкий гибридный semantic + FTS поиск (локальные эмбеддинги + опциональный Gemini `text-embedding-004`).
- [x] Автосвязи между материалами: общие категории/теги + общие инструменты/технологии (`shared_tool`) + семантическая близость (`semantic`).
- [x] Интерактивная визуализация графа знаний (`/graph`) с фильтрами и просмотром связанных материалов.
- [ ] Отдельная vector DB (Qdrant / Pinecone) — при росте базы свыше нескольких тысяч карточек.

---

## 4. Фаза 4 — SaaS + мобильное приложение (связь по аккаунту)

Цель: **один аккаунт → одна база знаний на web / мобиле / MCP / TG**, без «двух правд» (локальный SQLite vs облако).

### Принцип (заложить заранее, даже пока Lite)

| Слой | Решение |
|---|---|
| Identity | Аккаунт (email/OAuth/Apple/Google). Не «устройство = пользователь». |
| Data ownership | Материалы принадлежат `user_id` (позже `workspace_id` для агентства). |
| API | Уже почти есть (`/api/...` + Bearer). Мобилка = **тот же API**, не отдельный бэкенд. |
| Sync | Server is source of truth. Клиенты pull/push через API; локальный кэш (SQLite/Room) = offline replica, не вторая база. |
| Media | Файлы/моменты в object storage (S3) по `user_id/...`; CDN URL в карточке. Сейчас FS на VPS — абстракция storage уже намечена в ТЗ. |
| Auth | Access + refresh tokens (или session cookie web + secure storage mobile). Один token scope для MCP/mobile/web. |
| Lite → cloud | Экспорт/импорт или «upgrade»: миграция SQLite → Postgres с `user_id`; не ломать MCP mid-flight. |

### Мобильное приложение — продуктово

- **Не** форк логики карточек: только клиент (intake share sheet, список, карточка, поиск, опционально «слушать»).
- Share → Chronicum (iOS Share / Android intent) = главный mobile intake (Reels/файлы).
- Офлайн: читать кэш; очередь upload при сети.
- Push: «карточка готова» / «ошибка IG».

### Порядок внедрения (не раньше DoD personal MVP)

1. **Auth + multi-tenant API** (даже для одного себя в cloud) — фундамент.  
2. **Postgres + object storage** как cloud mode (Lite остаётся для solo offline).  
3. **Mobile client** на публичный API.  
4. Биллинг / workspaces — когда появится второй платящий пользователь.

### Чего не делать сейчас

- Отдельная «мобильная БД» или дублирующий sync-протокол в обход API.  
- Agency OS / биллинг до auth+sync.  
- Переписывать Next под React Native «всё в одном» — web остаётся кабинетом, mobile — тонкий клиент.

Детали по текущему фокусу (до SaaS): [SYSTEM_AUDIT.md](SYSTEM_AUDIT.md) P1.5 / §10 кадры.

---

## 5. Улучшения сверх ТЗ (бэклог идей)

Не блокируют MVP; брать по боли после личного использования.

### Продукт и UX
- ~~**Живая переписка с клиентом**~~ — **P2 MVP** (`/clients`, MCP `get_client_context`, TG `/client*`). См. [SYSTEM_AUDIT.md](SYSTEM_AUDIT.md) §9. Не CRM/Agency OS.
- **Карта архива** (`/graph`) — теги + рёбра по общим tools/links (не semantic KG).
- Еженедельный digest в бота: что сохранил, топ по score, pending-теги.
- Экспорт/импорт базы (JSON/Markdown) для смены VPS или бэкапа «вне Postgres».
- Команды бота для быстрых фильтров (`/tag`, `/category`, `/top`).
- Inline-кнопки: подтвердить тег / поднять score / сразу `/process_full` без команды.
- Шаринг одной карточки ссылкой (после появления веб-UI).
- [x] **Озвучка карточки (TTS):** кнопка «Слушать» на `/m/[id]` (Edge TTS с кэшем аудио).

### Контроль лимитов free tier
- [x] Мягкий дневной budget alert (`usage_alert` в `/health` и `/status`). Полные алерты по RPD/TPM/минутам Whisper — позже.
- [x] Автоfallback: при серии `429` на Groq LLM → пул ключей с cooldown и автоматический переход на Gemini (`GOOGLE_AI_API_KEY`).
- [ ] Дашборд usage за день/неделю в `/stats`.

### Качество контента
- [x] Обратная связь на ответ поиска («полезно / мимо») → сохранение в БД (`POST /api/search/feedback`) для тюнинга поиска/промпта.
- [ ] Повторный AI-анализ карточки после ручной правки raw_text.
- [ ] Язык карточки = язык исходника (явный detection).
- [x] Умный дедуп: канонический YouTube ID / нормализованный URL / SHA-256 файла.

### Интеграции (осторожно с ToS)
- Browser extension «Save to Chronicum».
- Share sheet / iOS Shortcut → Telegram или webhook.
- Официальный YouTube Data API вместо yt-dlp, где хватает метаданных + captions.

### Ops
- Мониторинг RAM на 2 GB VPS (alert при thrashing).
- Offsite-копия `pg_dump` (не только локальный `backups/`).
- Healthcheck endpoint + внешний uptime ping.

---

## 6. Порядок после успешного MVP

1. Закрыть DoD MVP (§3.11 ТЗ + пункты плана: two-stage, usage-логи, 429, бэкап, concurrency).
2. 2 недели daily use → собрать: какие источники реально шлются, где FTS слабый, где упираемся в Groq RPD/TPM.
3. Быстрые wins из §1 / §5 по фактической боли (обычно: веб-список карточек, digests, limit-alerts).
4. Решение go/no-go по **Фазе 2** (Agency OS). Не начинать Фазу 3/4 параллельно.

---

## 7. Pre-flight перед кодом MVP (лимиты Groq)

Не часть продуктового post-MVP, но обязательный шаг **до** фиксации `GROQ_LLM_MODEL` в `.env.example`.

1. Открыть под рабочим аккаунтом: [console.groq.com/settings/limits](https://console.groq.com/settings/limits).
2. Снять RPM / RPD / TPM / TPD (и ASH/ASD для Whisper) для кандидата LLM и Whisper.
3. Записать model id + дату проверки в README при создании репозитория кода.
4. Ориентир по публичной доке Groq (проверять в консоли — цифры могут отличаться по аккаунту): см. [GROQ_LIMITS_PREFLIGHT.md](GROQ_LIMITS_PREFLIGHT.md).
