<#
.SYNOPSIS
  One-time local setup without Docker (Windows).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Chronicum local setup" -ForegroundColor Cyan

# --- .env ---
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example — fill GROQ_API_KEY / TELEGRAM_BOT_TOKEN when ready."
} else {
  Write-Host ".env already exists"
}

# Force local hosts if someone copied docker values
$envContent = Get-Content ".env" -Raw
if ($envContent -match "@postgres:5432") {
  Write-Host "Note: DATABASE_URL still points at Docker hostname 'postgres'." -ForegroundColor Yellow
  Write-Host "      For local run use 127.0.0.1 (see .env.example)." -ForegroundColor Yellow
}

# --- dirs ---
New-Item -ItemType Directory -Force -Path "data\files" | Out-Null
New-Item -ItemType Directory -Force -Path "backups" | Out-Null
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

# --- Python venv ---
$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Host "Creating .venv ..."
  python -m venv .venv
}
& $venvPython -m pip install --upgrade pip | Out-Null
Write-Host "Installing Python deps ..."
& $venvPython -m pip install -r backend\requirements.txt

# --- Node ---
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm not found. Install Node.js 20+ from https://nodejs.org"
}
Write-Host "Installing web deps ..."
Push-Location web
if (-not (Test-Path ".env.local")) {
  Copy-Item ".env.local.example" ".env.local" -ErrorAction SilentlyContinue
}
npm install
Pop-Location

# --- Checks ---
Write-Host ""
Write-Host "==> Dependency checks" -ForegroundColor Cyan

function Test-Cmd($name) {
  if (Get-Command $name -ErrorAction SilentlyContinue) {
    Write-Host "  OK  $name"
    return $true
  }
  Write-Host "  MISSING  $name" -ForegroundColor Yellow
  return $false
}

$pgOk = Test-Cmd "psql"
$redisOk = (Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -WarningAction SilentlyContinue).TcpTestSucceeded
$pgPortOk = (Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue).TcpTestSucceeded
Test-Cmd "ffmpeg" | Out-Null
Test-Cmd "ffprobe" | Out-Null
Test-Cmd "tesseract" | Out-Null

if ($pgPortOk) { Write-Host "  OK  PostgreSQL :5432" } else { Write-Host "  MISSING  PostgreSQL on 127.0.0.1:5432" -ForegroundColor Yellow }
if ($redisOk) { Write-Host "  OK  Redis :6379" } else { Write-Host "  MISSING  Redis on 127.0.0.1:6379" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Install hints (Windows):" -ForegroundColor Cyan
Write-Host "  winget install PostgreSQL.PostgreSQL"
Write-Host "  winget install Memurai.MemuraiDeveloper   # Redis-compatible"
Write-Host "  winget install Gyan.FFmpeg"
Write-Host "  winget install UB-Mannheim.TesseractOCR"
Write-Host ""
Write-Host "Then create DB user/db (psql as postgres superuser):"
Write-Host "  CREATE USER chronicum WITH PASSWORD 'chronicum';"
Write-Host "  CREATE DATABASE chronicum OWNER chronicum;"
Write-Host ""
Write-Host "Next:  .\scripts\start-local.ps1"
