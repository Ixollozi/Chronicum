# Chronicum — запуск на ПК и открытие с телефона (LAN)

Как поднять стек (lite) на ПК и открыть веб-кабинет с телефона в той же Wi‑Fi.  
`<LAN-IP>` ниже — плейсхолдер; подставь свой локальный IPv4 Wi‑Fi (пример: `192.168.68.110`).

## 1. Обычный локальный старт (только ПК)

```powershell
cd <path-to-Chronicum>
# Пропиши GROQ_API_KEY (и при необходимости WEB_API_TOKEN) в .env
.\scripts\start-lite.ps1
```

Скрипт выводит команды запуска на **127.0.0.1** (API `:8000`, Web `:3000`). С телефона так не откроется.

## 2. Доступ с телефона (та же Wi‑Fi)

### Узнай LAN IP ПК

На Windows: `ipconfig` → строка **IPv4-адрес** адаптера Wi‑Fi (например, `192.168.68.110`).

### Настрой `.env` и `web/.env.local`

1. В корневом `.env` (читает backend через `backend/app/config.py`):

```env
# Смени дефолтный chronicum-dev-token на свой токен перед открытием в LAN
WEB_API_TOKEN=<your-strong-token>
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://<LAN-IP>:3000
NEXT_PUBLIC_API_URL=http://<LAN-IP>:8000
```

2. В `web/.env.local` (читает Next.js на старте dev-сервера):

```env
NEXT_PUBLIC_API_URL=http://<LAN-IP>:8000
# Оставь пустым для LAN, чтобы токен не попал в публичный JS-бандл Next.js
NEXT_PUBLIC_WEB_API_TOKEN=
```

> Если порт `8000` занят (например, можно поднять API на `8010`), замени `8000` на `8010` везде: в `uvicorn --port`, в `NEXT_PUBLIC_API_URL` и в правиле firewall.

### Слушай все интерфейсы (`0.0.0.0`)

Вместо `127.0.0.1` запусти API и Web на `0.0.0.0`:

```powershell
# Терминал 1 — Backend API (из корня репо)
. .\scripts\load-env.ps1
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# (или --port 8010, если 8000 занят)
```

```powershell
# Терминал 2 — Frontend Web (из корня репо)
cd web
npm run dev -- -H 0.0.0.0 -p 3000
```

### Открой порты в Windows Firewall

Добавь входящие правила (**Inbound Allow TCP**) для порта Web (**3000**) и порта API (**8000** — или **8010**, если используешь его), например в PowerShell от администратора:

```powershell
New-NetFirewallRule -DisplayName "Chronicum LAN TCP 3000" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
New-NetFirewallRule -DisplayName "Chronicum LAN TCP 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

### Вход в веб-кабинет (Google OAuth и Bearer / PAT токены)

Chronicum поддерживает два способа авторизации (проверяются в `backend/app/api/deps.py` через `Authorization: Bearer <token>`):

1. **Google OAuth + JWT-сессия (рекомендуется для основного кабинета):**
   - Задай `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `JWT_SECRET` и `WEB_ORIGIN=http://<LAN-IP>.nip.io:3000` (Google OAuth не принимает сырые приватные IP в Redirect URI, поэтому для входа через Google с телефона используй `nip.io`-домен и добавь `http://<LAN-IP>.nip.io:8010/api/auth/google/callback` в Authorized Redirect URIs в Google Cloud Console).
   - После входа через **Continue with Google** можно выпустить личный **Personal API Token (`chr_...`)** в **Настройки → Аккаунт** — он работает для MCP, скриптов и прямого входа по токену с любого устройства в LAN (включая сырой `<LAN-IP>`).
2. **Вход по Bearer-токену (`chr_...` PAT или `WEB_API_TOKEN`):**
   - Открой с телефона `http://<LAN-IP>:3000`.
   - Если Google OAuth не настроен — сразу откроется форма входа по токену; если Google OAuth включён — нажми **«Войти по API-токену»**.
   - Введи Personal API Token (`chr_...`) или `WEB_API_TOKEN` (при отключённом Google OAuth или `ALLOW_ADMIN_TOKEN=true`) и нажми **«Войти»** — кабинет проверит токен через API, сохранит его в `localStorage` (`chronicum_token`) и будет передавать в `Authorization: Bearer ...`. При ответе `401` сессия автоматически сбрасывается на экран входа.

> **Важно про безопасность:** переменные `NEXT_PUBLIC_*` вшиваются Next.js прямо в клиентский JS-бандл. Для локальной сети держи `NEXT_PUBLIC_WEB_API_TOKEN` пустым и вводи токен вручную на экране входа либо используй Google OAuth.

### URL с телефона

| Что | URL |
|---|---|
| Кабинет | `http://<LAN-IP>:3000` |
| API Health | `http://<LAN-IP>:8000/health` |
| API Docs | `http://<LAN-IP>:8000/docs` |

#### Опциональный DNS-алиас (`nip.io`)

Chronicum работает напрямую по сырому LAN IP — `nip.io` не обязателен. Но если удобнее доменное имя вместо IP, можно использовать `http://<LAN-IP>.nip.io:3000`: добавь `http://<LAN-IP>.nip.io:3000` в `CORS_ORIGINS` и укажи `NEXT_PUBLIC_API_URL=http://<LAN-IP>.nip.io:8000` (или `:8010`).

## 3. Чеклист

1. ПК и телефон в одной Wi‑Fi (не guest-сеть и без client isolation на роутере).
2. Backend (`:8000` или `:8010`) и Next.js (`:3000`) запущены на `0.0.0.0`.
3. В `.env` добавлен origin телефона в `CORS_ORIGINS`, задан свой `WEB_API_TOKEN`.
4. В `web/.env.local` указан `NEXT_PUBLIC_API_URL=http://<LAN-IP>:8000` (или `:8010`), а `NEXT_PUBLIC_WEB_API_TOKEN` пуст; после правок `next dev` перезапущен.
5. Windows Firewall пропускает входящий TCP на порт Web (`3000`) и порт API (`8000` / `8010`).
6. Telegram-бот запускается отдельно и требует реальный `TELEGRAM_BOT_TOKEN` (placeholder не стартует).
7. Публичный туннель (ngrok и т.п.) для LAN не нужен.

## 4. Подводные камни

- **Сменился Wi‑Fi / IP** → обнови `<LAN-IP>` в `CORS_ORIGINS` (`.env`) и `NEXT_PUBLIC_API_URL` (`web/.env.local`), затем перезапусти API и `next dev`.
- **Опечатка в токене или смена `WEB_API_TOKEN`** → форма входа проверяет токен перед сохранением, а при ответе API `401` кабинет автоматически сбрасывает токен в `localStorage` (`chronicum_token`) и возвращает на экран входа.
- **CORS-ошибка в браузере телефона** → проверь, что точный адрес из адресной строки телефона (`http://<LAN-IP>:3000` без завершающего `/`) есть в `CORS_ORIGINS` и backend перезапущен.
- **Только локальная сеть** → из мобильного интернета (LTE/5G) адрес `<LAN-IP>` недоступен.

## Пример

Если LAN IP ПК — `192.168.68.110`, а API поднят на порту `8000`:

- Кабинет: `http://192.168.68.110:3000`
- API: `http://192.168.68.110:8000/health`
- Опционально через `nip.io`: `http://192.168.68.110.nip.io:3000`
