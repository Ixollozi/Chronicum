# Load project-root .env into the current PowerShell process.
# Usage: . .\scripts\load-env.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning ".env not found at $envFile - copy from .env.example"
    return
}

Get-Content $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $name = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim().Trim('"').Trim("'")
    Set-Item -Path "Env:$name" -Value $val
}

# Lite defaults for local Windows
if (-not $env:CHRONICUM_LITE) { $env:CHRONICUM_LITE = "true" }
$dbPath = (Join-Path $root "data\chronicum.db") -replace "\\", "/"
if (-not $env:DATABASE_URL -or $env:DATABASE_URL -match "^\./") {
    $env:DATABASE_URL = "sqlite+aiosqlite:///$dbPath"
}
if (-not $env:STORAGE_PATH -or $env:STORAGE_PATH -match "^\./") {
    $env:STORAGE_PATH = Join-Path $root "data\files"
}

$keyLen = if ($env:GROQ_API_KEY) { $env:GROQ_API_KEY.Length } else { 0 }
$tgReady = $env:TELEGRAM_BOT_TOKEN -and ($env:TELEGRAM_BOT_TOKEN -notin @("replace_me", "changeme", "xxx"))
Write-Host ("Chronicum env loaded from .env (GROQ_API_KEY len={0}, telegram_ready={1})" -f $keyLen, $tgReady)
