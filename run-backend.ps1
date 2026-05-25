# Запуск бэкенда из корня репозитория
# powershell -ExecutionPolicy Bypass -File .\run-backend.ps1

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root

$logFile = Join-Path $Root "data\backend.log"

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: pyproject.toml not found in $Root" -ForegroundColor Red
    exit 1
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: .venv not found. Run .\install.ps1 first." -ForegroundColor Red
    exit 1
}

$logDir = Split-Path -Parent $logFile
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

Write-Host "==> video-pipeline backend (cwd=$Root)" -ForegroundColor Cyan
Write-Host "    http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host "    log: data\backend.log" -ForegroundColor DarkGray
Write-Host ""

if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
    Write-Host "WARNING: web/out/index.html missing - Launcher button 6 Build Web UI" -ForegroundColor Yellow
}

$env:TELEGRAM_ENABLED = "false"
Write-Host ""
Write-Host ">>> DO NOT CLOSE THIS WINDOW while Studio is open <<<" -ForegroundColor Yellow
Write-Host "    Wait for: Uvicorn running on http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host ""

Start-Transcript -Path $logFile -Append | Out-Null
try {
    & $py -m app.main
    $exitCode = $LASTEXITCODE
} finally {
    Stop-Transcript | Out-Null
}

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Backend exited with code $exitCode" -ForegroundColor Red
    Write-Host "Send file data\backend.log for help." -ForegroundColor Red
}
Write-Host ""
Write-Host "Press Enter to close this window..." -ForegroundColor Gray
Read-Host | Out-Null
