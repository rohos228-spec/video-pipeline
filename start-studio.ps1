# video-pipeline: локальная студия без Telegram-бота
# Запуск: powershell -ExecutionPolicy Bypass -File .\start-studio.ps1
#
# Поднимает: воркер + FastAPI (:8765). Telegram не нужен.
# Chrome нужен только когда реально гоняете шаги ChatGPT/outsee.

[CmdletBinding()]
param(
    [switch]$SkipChrome
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: запусти из корня video-pipeline." -ForegroundColor Red
    exit 1
}

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: venv не найден. Запусти .\install.ps1" -ForegroundColor Red
    exit 1
}

# Подсказка в .env
if (Test-Path ".env") {
    $raw = Get-Content ".env" -Raw
    if ($raw -notmatch "TELEGRAM_ENABLED") {
        Add-Content .env "`nTELEGRAM_ENABLED=false`n"
    }
}

if (-not $SkipChrome) {
    Write-Host "==> Chrome CDP (опционально для шагов GPT/outsee)" -ForegroundColor Cyan
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:29229/json/version" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { Write-Host "    [ok] Chrome :29229" -ForegroundColor Green }
    } catch {
        Write-Host "    [!] Chrome без CDP — шаги с браузером упадут, UI всё равно работает" -ForegroundColor Yellow
    }
}

Write-Host "==> video-pipeline studio (без Telegram)" -ForegroundColor Cyan
Write-Host "    API: http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host "    UI:  cd web; npm run dev  -> http://localhost:3000" -ForegroundColor Yellow
Write-Host ""

$env:TELEGRAM_ENABLED = "false"
& $venvPython -m app.main
