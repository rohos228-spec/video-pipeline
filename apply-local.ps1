# Локально: пересобрать UI + перезапустить бэкенд (БЕЗ git reset / pull).
#   powershell -ExecutionPolicy Bypass -File .\apply-local.ps1
#   powershell -ExecutionPolicy Bypass -File .\apply-local.ps1 -SkipBuild

param(
    [switch]$SkipBuild,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: запускай из корня video-pipeline (нет pyproject.toml)" -ForegroundColor Red
    exit 1
}

Write-Host "==> apply-local (no git reset)" -ForegroundColor Cyan
Write-Host "    repo: $Root" -ForegroundColor DarkGray

if (-not $SkipBuild) {
    if (-not (Test-Path "web\package.json")) {
        Write-Host "ERROR: web\package.json not found" -ForegroundColor Red
        exit 1
    }
    if (Test-Path "web\STUDIO_VERSION") {
        Write-Host "    STUDIO_VERSION:" -ForegroundColor DarkGray
        Get-Content "web\STUDIO_VERSION" -TotalCount 3 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    }
    Write-Host "==> npm run build" -ForegroundColor Cyan
    Push-Location web
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    Write-Host "    build OK" -ForegroundColor Green
} else {
    Write-Host "    skip build (-SkipBuild)" -ForegroundColor Yellow
}

$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    Write-Host "==> stop old backend" -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stop -Quiet
    Start-Sleep -Seconds 1
}

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8765"
    Write-Host "    browser: http://127.0.0.1:8765 (Ctrl+F5 posle starta)" -ForegroundColor Yellow
}

Write-Host "==> backend v etom zhe okne (Ctrl+C - ostanovka)" -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "run-backend.ps1") -NoPause
