# Groq limits — pre-flight и защитные механизмы

**Источник:** [console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits)  
**Аккаунт:** лимиты задаются на уровне организации; актуальные значения смотри в [console.groq.com/settings/limits](https://console.groq.com/settings/limits) — они имеют приоритет над ориентиром ниже.

---

## Ориентир Free Plan (публичная документация Groq)

| Model ID | RPM | RPD | TPM | TPD | ASH | ASD |
|---|---|---|---|---|---|---|
| `whisper-large-v3` | 20 | 2K | — | — | 7.2K | 28.8K |
| `whisper-large-v3-turbo` | 20 | 2K | — | — | 7.2K | 28.8K |
| `llama-3.3-70b-versatile` | 30 | **1K** | 12K | 100K | — | — |
| `llama-3.1-8b-instant` | 30 | **14.4K** | 6K | 500K | — | — |

- **Whisper:** ~2000 запросов/день, 7200 сек аудио/час и 28 800 сек/день; лимит размера одного файла — **25 МБ**.
- **LLM:** у `llama-3.3-70b-versatile` лучше качество structured JSON, 1K RPD достаточно для персонального использования (единицы–десятки материалов в день). При регулярных 429 по RPD/TPD можно переключить `GROQ_LLM_MODEL` или добавить ключи / Gemini в пул.

---

## Встроенная защита от лимитов в Chronicum

| Механизм | Настройка | Что экономит |
|---|---|---|
| **YouTube captions first-pass** | `YOUTUBE_PREFER_CAPTIONS=true` | Берёт субтитры / автосубтитры без скачивания медиа и с **0 сек Whisper ASH**; fallback на Whisper только если субтитров нет |
| **Чанкинг аудио перед Whisper** | `WHISPER_MAX_FILE_MB=20`, `WHISPER_CHUNK_SECONDS=600` | Не упирается в лимит 25 МБ на один файл при длинных видео и голосовых |
| **Пул ключей + fallback на Gemini** | Веб → Настройки → Ключи / `GOOGLE_AI_API_KEY` | Все LLM-вызовы (анализ карточки, обогащение, NL-поиск) автоматически переключаются между ключами Groq и далее на Gemini при 429/квоте |
| **Видео preview-режим (опционально)** | `VIDEO_PREVIEW_THRESHOLD_MIN=10`, `VIDEO_PREVIEW_FRACTION=0.2` | При выключенном тоггле «Видео целиком» для длинных роликов сначала разбирается ~20% длительности |

---

## Рекомендуемые значения `.env`

| Env | Дефолт | Комментарий |
|---|---|---|
| `TRANSCRIBE_PROVIDER` | `groq` | Whisper через Groq (или локальный fallback через субтитры YouTube) |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3` | Можно сменить на `whisper-large-v3-turbo` ради скорости |
| `AI_PROVIDER` | `groq` | Primary; автоматический fallback на `google`, если заданы ключи Gemini |
| `GROQ_LLM_MODEL` | `llama-3.3-70b-versatile` | Лучше держит JSON-схему карточки; резерв — `llama-3.1-8b-instant` |

---

## Чеклист проверки своего аккаунта Groq

Выполни один раз при подключении боевого ключа:

- [ ] Открыть [console.groq.com/settings/limits](https://console.groq.com/settings/limits) под своим org
- [ ] Сверить актуальные RPM/RPD/TPM/TPD для `GROQ_LLM_MODEL`
- [ ] Сверить Whisper ASH/ASD и при необходимости оставить `YOUTUBE_PREFER_CAPTIONS=true`
- [ ] Добавить запасной `GOOGLE_AI_API_KEY` (в `.env` или Веб → Настройки → Ключи) для бесшовного fallback при 429
