# Chronicum — системный аудит (идея → реализация)

Дата: 2026-09-17 · обновлено 2026-10-22 (лечение галлюцинаций: evidence-first)  
Связано: [TZ_AI_Knowledge_Base.md](TZ_AI_Knowledge_Base.md), [ROADMAP_POST_MVP.md](ROADMAP_POST_MVP.md), [QA_INSTAGRAM_LISTICLE_FINDINGS.md](QA_INSTAGRAM_LISTICLE_FINDINGS.md)

---

## Краткий вердикт (для быстрого чтения)

Идея **имеет смысл** — core loop живой. **P0–P1.6 (видео-правда) закрыты**; **P2 MVP landed**: карта архива (tools/links edges) + живой тред клиента (§9) + DX `:8010` + search feedback / Groq soft alert. **Лечение галлюцинаций landed (2026-10-22)**: evidence-сегменты + `analysis_confidence`, онлайн-проверка ссылок (`verified`), grounding tools/digest, PATCH+канон владельца, resolve клиентов без угадайки (ambiguous→кандидаты, дедуп 409), свежесть сводки, paste-импорт, MCP v2. Не пивотить в Agency OS / embeddings / мобилку.

### Имеет смысл оставить

- Knowledge base loop (web + MCP + опционально TG)
- Lite (SQLite) как основной режим
- Карточка + Whisper/OCR для видео
- YouTube / upload / статьи / PDF / заметки
- Key pool / usage — выживание на Groq
- **Graph `/graph`** — визуальная карта связей (Obsidian-like)
- **Кадры (moments)** — для screen-heavy / listicle; не ядро продукта

### Убрать / заморозить

| Что | Действие |
|---|---|
| Agency OS / режимы `task`/`rule` | Заморозить до DoD MVP |
| Глубже opinion/explain/evaluate | Не наращивать в приоритете |
| TikTok scrape | Upload-first |
| Vector DB / SaaS | По roadmap — рано |
| Pending-теги | **Оставить включёнными** (TG `/pending_tags` + keyboard) — не вырезать |

**IG:** scrape = best-effort; **класс A = файл + source_url**. Cookie-lock degrade уже есть.

### Улучшить (порядок) — post P2

1. Регрессии: держать green `qa_reel_githubradar.py` + unit fixtures при касаниях пайплайна  
2. Мобилка + аккаунт — [ROADMAP §4](ROADMAP_POST_MVP.md)  
3. Semantic KG / embeddings — только после нужды в поиске «по смыслу»  
4. Agency OS — заморожено  

**Итог:** personal knowledge MVP с картой архива + памятью о людях. Видео-правда listicle — на целевом классе; P2 DoD §9 — `get_client_context` / web `/clients` / TG `/client*`.

---

## Вердикт в одном абзаце

Идея **стоит продолжать**. Core loop собран. После спринта P0/P1 Reels-listicle с баннерами даёт правильные бренды/GitHub links и score ≤7; русский listicle (pronin) тоже ок. Instagram-по-ссылке всё ещё хрупкий (cookies), talking-head без речи — слабый. Фокус: **не расширять Agency**, а убрать шум (auto-moments, ASR-empty) и допилить находимость/Graph/threads.

---

## 1. Идея: что обещали vs что есть

| Обещание (ТЗ) | Сейчас |
|---|---|
| Saved-контент не умирает | Да, для YouTube / файлов / заметок / статей / Reels (лучше через upload) |
| Telegram → очередь → карточка → NL-поиск | Да (+ web как основной UI) |
| IG/TikTok scrape вне MVP | **В продукте**, IG улучшен (cookie degrade), TikTok хрупко |
| Agency OS (проекты/задачи) — Фаза 2 | Зачатки (`task`/`rule`) без настоящей системы |
| Knowledge graph — Фаза 3 | `/graph` = Obsidian-like карта, ещё не semantic KG |
| MCP для агентов | Есть — сильный плюс сверх ТЗ |

**Проблема реальная.** Риск — **доверие к карточке** на «плохих» видео (talking-head) и **ломкость IG URL**.

---

## 2. Core loop — где живёт ценность

```
Intake (TG / Web / MCP) → Queue → Extract (Whisper/OCR/…) → AI card → Search/Archive/Graph
                                                                    ↘ moments (опционально)
```

| Этап | Статус | Комментарий |
|---|---|---|
| Intake | Ок; IG URL best-effort | Upload + source_url; cookie-lock retry |
| Process | Ок на listicle / RU | EN talking-head / пустой Whisper — слабо |
| Moments | Полезны на screen-heavy | Шум на talking-head — сузить auto (§10) |
| Retrieve | Ок для ключевых слов | Не semantic memory; FTS / LIKE |
| Graph browse | Есть, полезен | Качество узлов = качество карточек |
| Taxonomy pending | Мёртвый код по сути | `auto_approve=True` |

---

## 3. Что имеет смысл оставить

1. **Идея personal knowledge base** — ядро продукта.
2. **Lite-режим** (SQLite + in-process queue).
3. **Карточка знания** (summary, digest, tools, links, score) — главный артефакт.
4. **Web + MCP**.
5. **Telegram** — если реально юзаешь; иначе вторичен к web.
6. **YouTube / upload video / article / PDF / note / voice**.
7. **Whisper + screen atlas** — чинить качество на edge-cases, не выкидывать.
8. **Groq key pool / usage / fallback**.
9. **QA-артефакты** + `scripts/qa_reel_githubradar.py`.
10. **Graph `/graph`**.
11. **Кадры** — оставить, но сделать умнее auto (§10).

---

## 4. Graph — в чём была «проблема» (уточнение)

### Коротко

**Ты хотел график — это нормально и это оставляем.**  
В аудите формулировка «заморозить Graph polish / не продавать как сеть знаний» **не значит «выпилить `/graph`»**. Она была про другое.

### Два разных «графа»

| | Что у тебя сейчас (`/graph`) | Что в ТЗ Фаза 3 «Knowledge Graph» |
|---|---|---|
| Узлы | Материалы + теги + категории | Материалы + смысловые связи, embeddings |
| Рёбра | тег/категория + **material↔material по общим tools/links** | «похож по смыслу», автосвязи, глубина |
| Аналог | **Obsidian graph** по тегам/заметкам | Semantic KG / vector neighbourhood |
| Код | `backend/app/services/graph.py`, `web/src/app/graph/` | pgvector / Qdrant + автолинки (ещё нет) |

Сейчас реализация ближе к **карте архива** (и в коде прямо написано Obsidian-like). Это уже полезный продукт: видеть кучи, кликать в карточку, фильтровать.

### В чём реальный риск (не «график плохой»)

1. **Ожидание vs реализация** — если в голове «умные связи между знаниями», а на экране «общие теги», будет ощущение недоделки *даже при красивом UI*.
2. **Качество графа = качество карточек** — кривые теги/категории → шумный граф. Listicle лучше после P1; talking-head/пустое ASR всё ещё шумят.
3. **Приоритет спринта** — полировка ForceGraph вторична к auto-gate кадров и EN ASR; экран `/graph` **не выкидывать**.

### Что делать с Graph (рекомендация после уточнения)

**Оставить и развивать как фичу «карта базы».**

Имеет смысл улучшить (~~сделано в P2~~):

- ~~Подписи/онбординг: «карта архива…»~~
- ~~Узлы-материалы кликабельны → `/m/[id]` + sheet «Открыть материал»~~
- Фильтры score/категория/поиск — уже есть; не ломать.
- ~~Рёбра material↔material по общим tools/links~~ — embeddings остаются Фазой 3 **поверх** текущего графа.

**Не делать:** вырезать `/graph`, прятать за флагом, подменять semantic KG.

---

## 5. Что убрать / заморозить / упростить

### Заморозить (не развивать 2–4 недели)

| Что | Почему |
|---|---|
| Agency OS / composer `task`/`rule` | Фаза 2 без DoD MVP; размывает фокус |
| Новые enrichment-фичи (opinion / explain / evaluate глубже) | Карточка и так широкая; сначала P1.5 (кадры/ASR-empty) |
| TikTok scrape | ToS + хрупкость; upload-first |
| Vector DB / SaaS / multi-user / **mobile** | По roadmap Фаза 4 — рано; схему «аккаунт → API → sync» уже зафиксировали в [ROADMAP §4](ROADMAP_POST_MVP.md) |
| Pending-tag workflow (если не включаете) | Либо починить и юзать, либо вырезать мёртвые команды |

### Упростить позиционирование

- **IG:** пока не надёжен scrape — первый класс = **upload файла + optional source_url**, scrape = best-effort.
- **Lite = продукт**; Docker/Postgres/Redis = «deploy later», не параллельная правда.
- Depth карточки: **standard по умолчанию**; detailed/opinion — по кнопке.
- **Graph:** позиционировать как карту архива (Obsidian-like); semantic KG — следующий слой, не замена.

### Можно скрыть в UI (не Graph)

- Режимы task/rule за флагом.

---

## 6. Что улучшить (приоритет)

### P0 / P1 — закрыто (2026-09-24)

См. [QA_INSTAGRAM_LISTICLE_FINDINGS.md](QA_INSTAGRAM_LISTICLE_FINDINGS.md). Cookie degrade, upload `source_url`, Groq health, ASR brands, GitHub links, moments/%, score ≤7, QA-gate githubradar — done. Spot-check: githubradar ✅, pronin ✅, buildwroman ⚠️ (пустой EN ASR).

### P1.5 / P1.6 — ~~done~~ (видео-правда)

Moments auto-gate, SPEECH_WEAK/OFFTOPIC, overlay tools, OCR link hygiene, strict digest — закрыты.

### P2 — ~~MVP landed~~ (2026-09-26)

1. ~~Порт/окружение~~ — start scripts / `NEXT_PUBLIC_API_URL` → `:8010`.
2. ~~Pending-теги~~ — **оставить** workflow в TG.
3. ~~Поиск «полезно / мимо»~~ — `POST /api/search/feedback` + кнопки на главной.
4. ~~Groq soft alert~~ — `usage_alert` в `/health` + строка в `/status`.
5. Регрессии reel — unit green; `qa_reel_githubradar.py` при касаниях пайплайна.
6. ~~Карта архива~~ — «Карта», легенда, sheet «Открыть материал», рёбра `tool`/`link`.
7. ~~Живая переписка (§9)~~ — `clients` / `client_messages`, API + MCP `get_client_context`, web `/clients`, TG `/client*`.
8. **Мобилка + аккаунт** — вне P2; [ROADMAP §4](ROADMAP_POST_MVP.md).

---

## 7. Риски

| Риск | Severity |
|---|---|
| Неверные сущности / score → потеря доверия | Medium (listicle лучше; talking-head ещё врёт тонко) |
| Instagram/TikTok ToS + ломкость scrape | High |
| Groq model churn / free limits | High |
| Dual lite vs full (два мира БД/очереди) | Medium |
| Scope creep → «много кнопок, слабая правда» | Strategic |
| Moments-шум на плохих видео → «фича мешает» | Medium — лечится auto-gate |
| Путаница «Obsidian-graph» vs «semantic KG» | Low–Medium |

---

## 8. Рекомендуемый фокус на спринт

**Сейчас (post-P2):** не расползаться в CRM/Agency; держать reel-регрессии зелёными; пользоваться `/clients` + MCP context в реальной переписке.

Не цель: Agency OS, vector DB, embeddings, мобилка.

**Критерий успеха P2 (закрыт):**

- `/graph` читается как карта архива; два материала с общим tool связаны  
- `get_client_context("Иван")` → сводка + recent messages без всего чата  
- web бьёт в `:8010`; search feedback + Groq soft alert на месте

---

## 9. Живая переписка с клиентом (сводка + кто это)

**Зачем:** не кидать нейронке весь чат каждый раз; чтобы она **узнавала клиента**, держала **актуальную сводку** и могла ответить «что с Иваном?» без ручной вставки истории.

**Не путать с Agency OS** (проекты/задачи/воронка). Это узко: **память о людях и переписке**.

### Как должно ощущаться

1. Есть карточка клиента (имя, как ещё зовут, контакты, сделка/контекст одной строкой).
2. Новые сообщения **дописываются** в тот же тред (web / Telegram / из Cursor), а не создают кучу отдельных заметок.
3. После пачки сообщений (или по кнопке «обновить сводку») нейронка **переписывает выжимку**: кто, чего хочет, договорённости, возражения, следующий шаг, открытые вопросы. Старое сырьё не выкидываем — лежит в архиве треда.
4. Когда спрашиваешь агента про клиента, он получает: **сводка + последние N сообщений + куски по поиску**, а не 50 страниц чата.

### Чтобы «реально понимала какого клиента»

| Слой | Смысл |
|---|---|
| Карточка клиента | Стабильный id + отображаемое имя + алиасы («Ваня», «Иван Петров», @tg) |
| Привязка сообщений | Каждое сообщение → этот id (ручной выбор или эвристика по контакту/названию чата) |
| Сводка | Отдельное поле, версия + дата обновления; при конфликте «сводка устарела» → дописать или пересчитать |
| Запрет путаницы | В промпт всегда имя + id; если в тексте два клиента — не смешивать без явного выбора |

### Минимальный набор для первой версии

- Один объект «клиент / тред» на человека (или на сделку — на выбор при создании).
- Добавить сообщение (текст; позже — голос/скрин).
- Авто- или ручное обновление сводки после append.
- Команда/MCP: «дай контекст по клиенту X» → сводка + last N + найденные куски.
- Поиск по сырым сообщениям внутри треда.

### Что сознательно отложить

- Полный CRM, стадии сделки, задачи, биллинг.
- Автоподхват всех переписок из WhatsApp/TG без явного «сохранить в Chronicum».
- Идеальное распознавание «это тот же человек» по голосу/лицу.

### Статус

**Статус: лечение landed.** MVP (P2): `clients` / `client_messages` + MCP. Лечение галлюцинаций: resolve без молчаливого выбора (`ambiguous` → кандидаты, 409 в API), дедуп при создании, PATCH/DELETE/merge, `speaker`/`event_at`, paste-импорт с превью и дедупом, свежесть сводки (`summary_stale`, покрытие), канон фактов владельца, история версий сводки, MCP `create_client`/`resolve_client`/`refresh_client_summary`.

**Готово:** спросил «что с клиентом X?» → сводка + recent + search hits без ручной вставки всего чата; при неоднозначности — вопрос, а не угадайка.

---

## 10. Кадры (key moments) — как работают, нужны ли, что улучшить

### Как работают (коротко)

1. После обработки видео (`auto_moments`, default on) → `build_key_moments` (`moments.py`).
2. Screen atlas (OCR по кадрам) + timed transcript.
3. Если **listicle** → picks по slug / шагам / ordinal speech / brand URL; иначе OCR-picks.
4. Fallback: речь / LLM JSON, если мало кадров.
5. Сохраняется `material.key_moments` + JPG в `materials/{id}/moments/`.
6. UI: вкладка «Кадры» (`MomentsGallery`) — только если массив не пуст. Ручной rebuild: «Ещё → Ключевые кадры».

Объект: `t_sec`, `caption`, `quote`, `visible_text`, `why`, `when_to_apply`, `image_relpath`.

### Нужны ли вообще

| Вердикт | Почему |
|---|---|
| **Оставить** | На listicle/туториале кадр = якорь «вот этот инструмент / этот шаг» быстрее, чем искать в транскрипте. Pronin/githubradar это подтверждают. |
| **Не ядро** | Ценность продукта = карточка (tools/links/digest/score). Кадры — ускоритель archive для screen-heavy. |
| **Не redesign** | Shot-boundary / embeddings — дорого и неясный ROI. Сначала сузить auto. |

Talking-head (buildwroman): 1 кадр с доменом — терпимо; лицо + пустой caption — вредят доверию → **не генерить**.

### Как улучшить (impact × effort)

| # | Идея | Impact | Effort |
|---|---|---|---|
| 1 | Auto-skip moments, если usable OCR кадров &lt; N **и** не listicle | High | Low |
| 2 | Hard-drop `%`-heavy frames до picks | High | Low |
| 3 | Подписи: slug/step first; слабый `when_to_apply` не показывать | Med | Low |
| 4 | UI: бейдж «мало текста на экране» / кнопка «пересобрать» | Med | Low–med |
| 5 | Классификатор talking-head vs UI-demo vs listicle | High | Med |
| 6 | Vision gate только на сомнительных picks (cost) | Med | Med |

**Рекомендация:** keep + **auto-gate (#1–2)** в следующем спринте; toggle «Автоскриншоты» в settings уже есть — оставить. Vision не включать по умолчанию.
