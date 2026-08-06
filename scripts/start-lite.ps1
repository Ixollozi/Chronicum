<#
.SYNOPSIS
  Instructions + env loader for Chronicum lite (Cursor terminal, no extra windows).

.EXAMPLE
  # In Cursor terminal #1 (API):
  . .\scripts\load-env.ps1
  cd backend
  ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

  # Terminal #2 (web):
  cd web
  npm run dev -- -H 127.0.0.1 -p 3000

  # Terminal #3 (Telegram, when TELEGRAM_BOT_TOKEN set):
  . .\scripts\load-env.ps1
  cd backend
  ..\.venv\Scripts\python.exe -m app.bot.main
#>
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\load-env.ps1"

Write-Host @"

Не запускай через Start-Process — только Cursor terminal.

API:
  . .\scripts\load-env.ps1
  cd backend
  ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Web:
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
