$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "data\chronicum.db"
$destDir = Join-Path $root "backups"
if (-not (Test-Path $src)) {
    Write-Error "SQLite not found: $src (lite mode stores the DB here)"
}
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$out = Join-Path $destDir "chronicum_lite_$stamp.db"
Copy-Item $src $out
Get-ChildItem $destDir -Filter "chronicum_lite_*.db" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 14 |
    Remove-Item -Force
Write-Host "Backup written: $out"
