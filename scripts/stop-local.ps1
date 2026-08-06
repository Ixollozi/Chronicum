<#
.SYNOPSIS
  Stop processes started by start-local.ps1
#>
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $Root "logs\local.pids"

if (-not (Test-Path $pidFile)) {
  Write-Host "No logs\local.pids — nothing to stop (or start manually killed)."
  exit 0
}

Get-Content $pidFile | ForEach-Object {
  $procId = $_.Trim()
  if (-not $procId) { return }
  try {
    Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop
    Write-Host "Stopped PID $procId"
  } catch {
    Write-Host "PID $procId already gone"
  }
}

Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "Done."
