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

if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
    Write-Host "WARNING: web/out/index.html missing - UI will show build instructions." -ForegroundColor Yellow
    Write-Host "         In Studio menu: button 6 Build Web UI" -ForegroundColor Yellow
    Write-Host ""
}

$env:TELEGRAM_ENABLED = "false"
Write-Host "TELEGRAM_ENABLED=false (web-only Studio mode)" -ForegroundColor DarkGray
Write-Host ""

& $py -m app.main
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Backend exited with code $LASTEXITCODE" -ForegroundColor Red
}
Write-Host ""
Write-Host "Press Enter to close this window..." -ForegroundColor Gray
Read-Host | Out-Null
