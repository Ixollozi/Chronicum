<#
.SYNOPSIS
  Start Chronicum locally without Docker (API, worker, bot, web).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  throw "Missing .venv. Run: .\scripts\setup-local.ps1"
}
if (-not (Test-Path ".env")) {
  throw "Missing .env. Run: .\scripts\setup-local.ps1"
}

New-Item -ItemType Directory -Force -Path "data\files", "logs" | Out-Null

# Load simple KEY=VALUE from .env into process env for child procs
Get-Content ".env" | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#")) { return }
  $i = $line.IndexOf("=")
  if ($i -lt 1) { return }
  $k = $line.Substring(0, $i).Trim()
  $v = $line.Substring($i + 1).Trim()
  [Environment]::SetEnvironmentVariable($k, $v, "Process")
}

# Absolute storage path for Windows
if (-not $env:STORAGE_PATH -or $env:STORAGE_PATH -eq "./data/files" -or $env:STORAGE_PATH -eq ".\data\files") {
  $env:STORAGE_PATH = Join-Path $Root "data\files"
}

Write-Host "==> Checking Postgres / Redis ..." -ForegroundColor Cyan
$pgOk = (Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue).TcpTestSucceeded
$redisOk = (Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -WarningAction SilentlyContinue).TcpTestSucceeded
if (-not $pgOk) { throw "PostgreSQL not reachable on 127.0.0.1:5432" }
if (-not $redisOk) { throw "Redis not reachable on 127.0.0.1:6379 (try Memurai / Redis for Windows)" }

Write-Host "==> Alembic migrate ..." -ForegroundColor Cyan
Push-Location backend
& $venvPython -m alembic upgrade head
Pop-Location

$jobs = @()

function Start-LoggedProcess($Name, $FilePath, $ArgumentList, $WorkDir) {
  $out = Join-Path $Root "logs\$Name.out.log"
  $err = Join-Path $Root "logs\$Name.err.log"
  Write-Host "Starting $Name ..."
  return Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkDir `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Minimized
}

$procs = @()
$apiPort = if ($env:CHRONICUM_API_PORT) { $env:CHRONICUM_API_PORT } else { "8010" }
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:$apiPort"
$procs += Start-LoggedProcess "api" $venvPython @("-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", $apiPort) (Join-Path $Root "backend")
$procs += Start-LoggedProcess "worker" $venvPython @("-m", "arq", "app.workers.settings.WorkerSettings") (Join-Path $Root "backend")
$procs += Start-LoggedProcess "bot" $venvPython @("-m", "app.bot.main") (Join-Path $Root "backend")

$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCmd) { $npmCmd = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $npmCmd) { throw "npm not found" }
$procs += Start-LoggedProcess "web" $npmCmd.Source @("run", "dev", "--", "-H", "127.0.0.1", "-p", "3000") (Join-Path $Root "web")

# Persist PIDs for stop script
$procs | ForEach-Object { $_.Id } | Set-Content (Join-Path $Root "logs\local.pids")

Write-Host ""
Write-Host "Chronicum is starting:" -ForegroundColor Green
Write-Host "  Web   http://127.0.0.1:3000"
Write-Host "  API   http://127.0.0.1:$apiPort/health"
Write-Host "  Logs  $Root\logs\*.log"
Write-Host "  Stop  .\scripts\stop-local.ps1"
Write-Host ""
Write-Host "Token for web login: $($env:WEB_API_TOKEN)"
