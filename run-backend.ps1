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

Write-Host "TELEGRAM_ENABLED=false (web-only Studio mode)" -ForegroundColor DarkGray
Write-Host ""
Write-Host ">>> DO NOT CLOSE THIS WINDOW while Studio is open <<<" -ForegroundColor Yellow
Write-Host "    When you see 'Uvicorn running on http://127.0.0.1:8765' — open browser." -ForegroundColor Yellow
Write-Host ""

& $py -m app.main
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Backend exited with code $exitCode" -ForegroundColor Red
    Write-Host "Copy ALL text above (especially Traceback/ERROR) and send for help." -ForegroundColor Red
} else {
    Write-Host ""
    Write-Host "Backend stopped normally." -ForegroundColor Gray
}
Write-Host ""
Write-Host "Press Enter to close this window..." -ForegroundColor Gray
Read-Host | Out-Null
