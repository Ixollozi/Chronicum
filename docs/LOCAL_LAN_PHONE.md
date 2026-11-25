# Chronicum — запуск на ПК и открытие с телефона

Как поднять стек (lite) и открыть UI с телефона в той же Wi‑Fi.  
`<LAN-IP>` ниже — плейсхолдер; подставь свой IPv4 Wi‑Fi (пример: `192.168.68.110`).

## 1. Обычный локальный старт (только ПК)

```powershell
cd <path-to-Chronicum>
# GROQ_API_KEY и Google OAuth уже в .env
.\scripts\start-lite.ps1
```

По умолчанию скрипт слушает **127.0.0.1** (часто порт 8000 в скрипте). С телефона так не откроется.

## 2. Доступ с телефона (та же Wi‑Fi)

### Слушать все интерфейсы

Вместо localhost:

- Backend: `uvicorn … --host 0.0.0.0 --port 8010`
- Frontend: `next dev -H 0.0.0.0 -p 3000`

### Env (локально, под LAN)

В корневом `.env` и `web/.env.local` (подставь свой IP):

```env
NEXT_PUBLIC_API_URL=http://<LAN-IP>.nip.io:8010
WEB_ORIGIN=http://<LAN-IP>.nip.io:3000
GOOGLE_REDIRECT_URI=http://<LAN-IP>.nip.io:8010/api/auth/google/callback
CORS_ORIGINS=...,http://<LAN-IP>.nip.io:3000,http://<LAN-IP>:3000
```

Узнать LAN IP на Windows: `ipconfig` → IPv4 Wi‑Fi.

### Firewall

Inbound Allow TCP **3000** и **8010** (имена вроде `Chronicum LAN TCP 3000` / `8010`).

### Почему nip.io, а не сырой IP

Google OAuth **не принимает** redirect на `http://192.168.x.x/...` (нужен публичный TLD).  
`http://192.168.x.x.nip.io` резолвится в тот же LAN IP — Google принимает, трафик остаётся в локальной сети.

В Google Cloud Console (OAuth client) добавь:

- Redirect: `http://<LAN-IP>.nip.io:8010/api/auth/google/callback`
- JS origin: `http://<LAN-IP>.nip.io:3000`  
(плюс при желании `127.0.0.1` для работы только с ПК)

### URL с телефона

| Что | URL |
|---|---|
| Кабинет | `http://<LAN-IP>.nip.io:3000` |
| API | `http://<LAN-IP>.nip.io:8010` |
| Docs | `http://<LAN-IP>.nip.io:8010/docs` |

Сырой `http://<LAN-IP>:3000` может открыть страницу, но **логин Google** — только через nip.io-хост.

## 3. Чеклист

1. ПК и телефон в одной Wi‑Fi (не guest/изолированная сеть).
2. API и web на `0.0.0.0`, firewall открыт.
3. Env и Google Console на nip.io.
4. Telegram-бот — отдельно, нужен реальный `TELEGRAM_BOT_TOKEN` (placeholder не стартует).
5. Публичный туннель (ngrok) не обязателен для LAN.

## 4. Подводные камни

- Сменился Wi‑Fi / IP → обнови env, nip.io-хост и Google Console.
- `scripts/load-env.ps1` может снова выставить `NEXT_PUBLIC_API_URL` на `127.0.0.1` — для LAN переопределяй при старте / в `.env.local`.
- Это не доступ из мобильного интернета: только LAN.

## Пример

LAN IP `192.168.68.110`:

- http://192.168.68.110.nip.io:3000  
- http://192.168.68.110.nip.io:8010  
