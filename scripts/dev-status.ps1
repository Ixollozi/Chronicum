# Daily readiness check for Chronicum lite (Cursor terminal).
# Usage: . .\scripts\load-env.ps1; .\scripts\dev-status.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
if (-not $env:GROQ_API_KEY) {
    . (Join-Path $PSScriptRoot "load-env.ps1")
}

function Test-Cmd($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

Write-Host "=== Chronicum status ==="
Write-Host ("GROQ_API_KEY:     {0}" -f ($(if ($env:GROQ_API_KEY -and $env:GROQ_API_KEY -notin @("replace_me","changeme")) { "ok (len=$($env:GROQ_API_KEY.Length))" } else { "MISSING" })))
Write-Host ("TELEGRAM_TOKEN:   {0}" -f ($(if ($env:TELEGRAM_BOT_TOKEN -and $env:TELEGRAM_BOT_TOKEN -notin @("replace_me","changeme")) { "ok" } else { "placeholder - put later" })))
Write-Host ("TELEGRAM_OWNER:   {0}" -f ($(if ($env:TELEGRAM_OWNER_ID -and $env:TELEGRAM_OWNER_ID -ne "0") { $env:TELEGRAM_OWNER_ID } else { "0 (open until set)" })))
Write-Host ("ffmpeg:           {0}" -f ($(if (Test-Cmd ffmpeg) { "ok" } else { "missing (optional)" })))
Write-Host ("ffprobe:          {0}" -f ($(if (Test-Cmd ffprobe) { "ok" } else { "missing (optional)" })))
Write-Host ("tesseract:        {0}" -f ($(if (Test-Cmd tesseract) { "ok" } else { "missing (optional OCR)" })))
Write-Host ("pdftoppm:         {0}" -f ($(if (Test-Cmd pdftoppm) { "ok" } else { "missing (optional PDF OCR / Poppler)" })))

$port = if ($env:CHRONICUM_API_PORT) { $env:CHRONICUM_API_PORT } else { "8010" }
$api = "http://127.0.0.1:$port/health"
try {
    $h = Invoke-RestMethod -Uri $api -TimeoutSec 3
    Write-Host ("API :{0}:        ok mode={1} ai_ready={2}" -f $port, $h.mode, $h.ai_ready)
} catch {
    Write-Host ("API :{0}:        DOWN" -f $port)
}

try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 3
    Write-Host "Web :3000:        ok"
} catch {
    Write-Host "Web :3000:        DOWN (or not started)"
}

Write-Host ""
Write-Host "Start (Cursor terminals):"
Write-Host "  . .\scripts\load-env.ps1"
Write-Host ("  cd backend; ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port {0}" -f $port)
Write-Host "  . .\scripts\load-env.ps1; cd web; npm run dev -- -H 127.0.0.1 -p 3000"
Write-Host "  cd backend; ..\.venv\Scripts\python.exe -m app.bot.main"
