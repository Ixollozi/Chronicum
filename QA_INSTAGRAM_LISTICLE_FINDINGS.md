# QA: Instagram listicle / Reels — findings + post-fix

Дата: 2026-09-16 (исходный QA) · обновлено 2026-09-26 (P1.6 ideal cards + tusharx reprocess)  
Связано: [SYSTEM_AUDIT.md](SYSTEM_AUDIT.md), `scripts/qa_reel_githubradar.py`

---

## Вердикт сейчас

**P0/P1/P1.5/P1.6 закрыты.** Listicle + RU речь → сильная карточка; overlay-бренды в tools; offtopic speech (ad-jingle) не раздувает digest; moments на text-heavy non-listicle.

| Ролик | Тип | Карточка | Score | Moments |
|---|---|---|---|---|
| githubradar `DdOPmR8Au0j` | listicle + GitHub баннеры | сильно | 7 | >0 |
| pronin.media `DcLU7DfMlen` | listicle + домены | хорошо | 5 | 5 |
| buildwroman `DZgDULUS8av` | talking-head EN (pre-P1.5) | слабо | 3 | 1 |
| alhimik_ai (P1.5) | RU tutorial OmniFlash | хорошо | 7 | **0** |
| tusharx.ai (**P1.6**) | EN promo + Oreo audio | **хорошо** (tools) | **3** | **4** |
| r.fenomen (P1.5) | RU Cloth + screen UI | хорошо | 7 | 3 |

---

## Эталон githubradar (ручной)

| # | Slug | Суть |
|---|---|---|
| 1 | `rybbit-io/rybbit` | Self-hosted analytics |
| 2 | `BoardUI/boardui` | React UI kit |
| 3 | `pranshuparmar/witr` | CLI: кто держит порт/процесс |

Хвост = промо ТГ GitHub Radar, не 4-й пункт. Ручная оценка: **~6–7/10**.

Материал: `e5a2b7d8-cd85-4363-afd3-0f5b1b04b74e` · артефакты `data/qa-githubradar/` · QA-gate: `scripts/qa_reel_githubradar.py`

---

## До фикса (2026-09-16) → после P0/P1 (2026-09-24)

| Поле | Было | Стало |
|---|---|---|
| Brands | Rybit / Vitter | Rybbit / witr / BoardUI |
| Links | rybbit.io + мусор Wcrybbit | + `github.com/rybbit-io/rybbit`, BoardUI, witr |
| Moments | `%` / `Wcrybbit` | речь по проектам + brand frames (не %-доминация) |
| Score | 10/10 | **7** (cap promo-listicle) |
| IG URL | cookie lock hard-fail | degrade + retry без browser cookies; upload + `source_url` |
| Groq model | llama 404 | `openai/gpt-oss-120b` + `/health` `groq_llm_ok` |

---

## Spot-check ×3 (Saved «Бизнесс и ИИ»)

### 1. githubradar — baseline

IG: жёлтый баннер `rybbit-io/rybbit`.  
Chronicum: title/tools/links/score 7 совпадают с эталоном. Gate **OK**.

### 2. pronin.media — listicle с доменами

IG: «где вайбкодеру брать дизайн-систему / шрифты / иконки».  
Chronicum (`3ec53df3-…`): ASR полный; tools/links — tweakcn, mobbin, Google Fonts, storybook, shadcn, fontshare; **5 moments** по доменам; score 5.  
→ Пайплайн OCR+речь+links/moments на русском listicle **работает**.

### 3. buildwroman — talking-head EN

IG: talking head, caption #claude #marketing.  
Chronicum (`06ad1715-…`): Whisper ≈ «Спасибо за просмотр!»; OCR → Outlier-app (+ опечатка `tryoutier`); Claude из хэштегов/LLM; score **3**; 1 moment.  
→ Не crash, но **мало ценности** без речи.

---

## Spot-check ×3 новых (P1.5, Saved «Бизнесс и ИИ», ixollozi)

Не брали уже сравненные: githubradar / buildwroman / pronin. API `:8010`, web `:3000`, Playwright **personal** (Default).

### 1. alhimik_ai — RU tutorial OmniFlash

IG: бесплатный монтаж через Google OmniFlash / Flow.  
Chronicum `a8486220-4627-40fa-bb9d-20fdb4949693`: ASR нормальный RU (не «Спасибо…»); tools Google Flow / OmniFlash / Telegram; links `labs.google` + TG; score **7**; **moments 0** (talking-head / мало usable OCR → auto-gate).  
→ Речь + digest ок; кадры правильно пустые.

### 2. tusharx.ai — EN promo Claude / Seedance

IG эталон: split-screen **Gemini Omni vs Seedance 2.0**, оверлей Claude Fabel, CTA Comment CLAUDE; речь = Oreo jingle (не тема).  
Chronicum `dd0c4a9e-…` **после P1.6**: tools **Claude / Gemini Omni / Seedance**; links без bare `google.com`; digest ≤3, без Oreo; score **3**; **4 moments** по brand-overlay.  
→ Эталон vs карточка: бренды с экрана совпадают; шаги из jingle не выдумываются.

### 3. r.fenomen — RU Cloth / Figma

IG: карусели через Cloth + Figma/Pencil на экране ноутбука.  
Chronicum `24dd3575-9bfc-4707-a337-91fb85713763`: ASR полный RU; tools Cloth / Figma / Pencil / Notion; links cloth.ai / figma / pencil; score **7**; **3 moments** (screen UI).  
→ Listicle/tutorial с экраном — moments уместны.

---

## Чеклист (статус)

### P0 — закрыто

- [x] IG cookie-lock → retry без `cookiesfrombrowser`
- [x] Upload + optional `source_url`
- [x] Дефолт модели + lifespan/health `groq_llm_ok`

### P1 — закрыто (listicle)

- [x] ASR brand normalize + post-LLM brand fix
- [x] GitHub slug harvest + brand→repo fallback (githubish context)
- [x] Moments: slug boost, % demote, ordinal speech, CTA zone
- [x] Score cap ≤7 для shallow promo-listicle
- [x] Unit tests + `qa_reel_githubradar.py` + Playwright spot-check

### P1.5 — закрыто (2026-09-26)

- [x] ASR hallucination filter + EN retry + skip localize junk
- [x] Empty/weak speech → short caption/OCR digest (`SPEECH_WEAK`)
- [x] Moments auto-skip talking-head (non-listicle + usable OCR &lt; 2 → `[]`)
- [x] OCR junk hosts + near-dup hostname hygiene
- [x] Unit tests `test_reel_pipeline_quality` + `test_social_sources` green
- [x] Playwright personal Saved ≥3 новых (ниже)

### P1.6 — закрыто (2026-09-26)

- [x] Overlay product harvest + AI brands (Gemini Omni / Seedance / OmniFlash / …)
- [x] `SPEECH_OFFTOPIC` + digest ≤3 + refresh tools after moments OCR
- [x] Moments gate: `overlay_n` unlocks text-heavy non-listicle; skip speech fills when offtopic
- [x] Demote bare `google.com` apex links
- [x] Unit tests + tusharx reprocess + IG эталон compare

### Ещё улучшить (P2+)

1. Category не всегда `AI` / Programming для devops/analytics mix.
2. Порт fallback `:8000`→`:8010` в start-скриптах (`NEXT_PUBLIC_API_URL` в shell env перебивает `.env.local`).
3. IG scrape всё ещё best-effort; класс A = файл + `source_url`.

---

## Кадры (moments) — кратко

**Что это:** автоскриншоты ключевых моментов видео (OCR atlas → listicle/OCR picks → речь/LLM fallback) → вкладка «Кадры».

| Когда полезны | Когда мусор |
|---|---|
| Listicle / туториал с текстом на экране (pronin, githubradar) | Talking-head / подкаст (buildwroman) |
| Баннеры slug / домены = якоря архива | %-дашборды, CTA «подпишись» |

**Нужны ли:** да, **как опция для screen-heavy видео**, не как обязательный wow. Не выкидывать и не redesign; **сузить auto** (skip если нет usable OCR и не listicle). Детали — [SYSTEM_AUDIT.md](SYSTEM_AUDIT.md) §10.

---

## Код / тесты

- Cookies / ytdlp: `backend/app/pipelines/youtube.py`
- Harvest / ASR: `backend/app/services/resources.py`
- Moments: `backend/app/services/moments.py`
- Score: `backend/app/ai/schemas.py`
- Gates: `backend/tests/test_reel_pipeline_quality.py`, `scripts/qa_reel_githubradar.py` (+ `qa_reel_sergienko.py`)
