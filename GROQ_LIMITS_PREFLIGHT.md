# Groq limits — pre-flight для MVP

**Дата сверки публичной доки:** 2026-08-06  
**Источник:** [console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits)  
**Аккаунт:** лимиты org-level; точные цифры смотреть в [console.groq.com/settings/limits](https://console.groq.com/settings/limits) — они имеют приоритет над этой таблицей.

Код `.env.example` создан с дефолтом `llama-3.3-70b-versatile` (сверка публичной доки 2026-08-06). Перед продомом подтверди цифры в своей консоли.


---

## Ориентир Free Plan (публичная дока Groq)

| Model ID | RPM | RPD | TPM | TPD | ASH | ASD |
|---|---|---|---|---|---|---|
| `whisper-large-v3` | 20 | 2K | — | — | 7.2K | 28.8K |
| `whisper-large-v3-turbo` | 20 | 2K | — | — | 7.2K | 28.8K |
| `llama-3.3-70b-versatile` | 30 | **1K** | 12K | 100K | — | — |
| `llama-3.1-8b-instant` | 30 | **14.4K** | 6K | 500K | — | — |

Whisper: ~2000 audio req/day и 7200 audio seconds/hour — комфортно для personal daily use.

LLM: у 70B ниже RPD (1K/day), у 8B — сильно выше RPD, но слабее качество structured JSON. Для Chronicum (единицы–десятки материалов/день) **1K RPD на 70B достаточно**.

---

## Рекомендация до создания `.env.example`

| Env | Кандидат | Почему |
|---|---|---|
| `TRANSCRIBE_PROVIDER` | `groq` | По ТЗ |
| Whisper model | `whisper-large-v3` (или `whisper-large-v3-turbo` если нужен запас по скорости) | Free tier, без нагрузки на VPS |
| `AI_PROVIDER` | `groq` | Primary; hot-swap `google` без смены кода |
| `GROQ_LLM_MODEL` | **`llama-3.3-70b-versatile`** | Лучше для schema JSON; 1K RPD ок для personal use |
| Fallback LLM | `llama-3.1-8b-instant` | Если в консоли у аккаунта 70B ужесточён или частые 429 по RPD/TPD |

При устойчивых `429` / невалидном JSON → сменить модель через `.env` или `AI_PROVIDER=google`.

---

## Чеклист (нужно сделать один раз под своим аккаунтом)

Перед тем как писать дефолт модели в `.env.example` при старте кода:

- [ ] Открыть [console.groq.com/settings/limits](https://console.groq.com/settings/limits) под рабочим org
- [ ] Сверить RPM/RPD/TPM/TPD для выбранной LLM (не полагаться только на статьи)
- [ ] Сверить Whisper ASH/ASD
- [ ] Подтвердить или сменить `GROQ_LLM_MODEL` относительно рекомендации выше
- [ ] Записать в README дату проверки и model id

**Статус pre-flight в репо:** публичная дока сверена 2026-08-06; **account-specific confirm — TBD владельцем** при первом деплое ключа.
