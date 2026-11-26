<#
.SYNOPSIS
  Instructions + env loader for Chronicum lite (Cursor terminal, no extra windows).

  API listens on :8010 by default (avoids clash with other projects on :8000).
  load-env.ps1 forces NEXT_PUBLIC_API_URL to match — do not leave a stale :8000 in the shell.

.EXAMPLE
  # In Cursor terminal #1 (API):
  . .\scripts\load-env.ps1
  cd backend
  ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010

  # Terminal #2 (web) — after load-env so NEXT_PUBLIC_API_URL=:8010:
  . .\scripts\load-env.ps1
  cd web
  npm run dev -- -H 127.0.0.1 -p 3000

  # Terminal #3 (Telegram, when TELEGRAM_BOT_TOKEN set):
  . .\scripts\load-env.ps1
  cd backend
  ..\.venv\Scripts\python.exe -m app.bot.main
#>
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\load-env.ps1"
$port = if ($env:CHRONICUM_API_PORT) { $env:CHRONICUM_API_PORT } else { "8010" }

Write-Host @"

Не запускай через Start-Process — только Cursor terminal.
API порт: $port (NEXT_PUBLIC_API_URL=$($env:NEXT_PUBLIC_API_URL))

API:
  . .\scripts\load-env.ps1
  cd backend
  ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $port

Web (обязательно . load-env.ps1 в той же сессии, иначе Next может взять :8000 из shell):
  . .\scripts\load-env.ps1
  cd web
  npm run dev -- -H 127.0.0.1 -p 3000

Telegram (нужен TELEGRAM_BOT_TOKEN + TELEGRAM_OWNER_ID в .env):
  . .\scripts\load-env.ps1
  cd backend
  ..\.venv\Scripts\python.exe -m app.bot.main

Login token веб: $($env:WEB_API_TOKEN)
Проверка окружения: .\scripts\dev-status.ps1
Бот без токена подождёт .env; после старта /whoami → TELEGRAM_OWNER_ID
ffmpeg (опц.): winget install Gyan.FFmpeg
"@
