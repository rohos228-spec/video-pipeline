# Запуск бэкенда из корня репозитория (безопасно после cd web)
# powershell -ExecutionPolicy Bypass -File .\run-backend.ps1

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: pyproject.toml not found in $Root" -ForegroundColor Red
    exit 1
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: .venv not found. Run .\install.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "==> video-pipeline backend (cwd=$Root)" -ForegroundColor Cyan
Write-Host "    http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host ""

if (-not $env:TELEGRAM_ENABLED) { $env:TELEGRAM_ENABLED = "false" }
& $py -m app.main
