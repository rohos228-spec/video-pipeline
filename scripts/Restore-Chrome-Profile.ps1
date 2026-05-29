# Перенос профиля Chrome с другого ПК (где уже были логины).
param(
    [Parameter(Mandatory = $true)]
    [string]$FromPath
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\VpBrowserProfile.ps1"

$src = $FromPath.Trim('"')
if (-not (Test-Path $src)) {
    Write-Host "ERROR: не найдено: $src" -ForegroundColor Red
    exit 1
}

$dest = Get-VpBrowserUserDataDir
Write-Host ""
Write-Host "Копирую профиль Chrome:" -ForegroundColor Cyan
Write-Host "  из: $src"
Write-Host "  в:  $dest"
Write-Host ""

$chrome = Get-Process -Name chrome -ErrorAction SilentlyContinue
if ($chrome) {
    Write-Host "Закрой ВСЕ окна Chrome и нажми Enter..." -ForegroundColor Yellow
    Read-Host | Out-Null
}

if (Test-Path $dest) {
    $backup = "$dest.backup_$(Get-Date -Format yyyyMMdd_HHmmss)"
    Write-Host "Бэкап текущего профиля -> $backup" -ForegroundColor DarkGray
    Move-Item -LiteralPath $dest -Destination $backup
}

New-Item -ItemType Directory -Path $dest -Force | Out-Null
Copy-Item -Path (Join-Path $src "*") -Destination $dest -Recurse -Force

Write-Host ""
Write-Host "[ok] Профиль скопирован. Запусти Start-Chrome.cmd" -ForegroundColor Green
Write-Host ""
