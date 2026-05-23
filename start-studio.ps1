# video-pipeline: local studio without Telegram bot
# Run: powershell -ExecutionPolicy Bypass -File .\start-studio.ps1
#
# Starts: worker + FastAPI (:8765). Telegram is not required.
# Chrome CDP is only needed for ChatGPT/outsee pipeline steps.

[CmdletBinding()]
param(
    [switch]$SkipChrome
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: run from video-pipeline root folder." -ForegroundColor Red
    exit 1
}

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: venv not found. Run .\install.ps1 first." -ForegroundColor Red
    exit 1
}

if (Test-Path ".env") {
    $raw = Get-Content ".env" -Raw -Encoding UTF8
    if ($raw -notmatch "TELEGRAM_ENABLED") {
        Add-Content -Path ".env" -Value "`nTELEGRAM_ENABLED=false`n" -Encoding UTF8
    }
}

if (-not $SkipChrome) {
    Write-Host "==> Chrome CDP (optional for GPT/outsee steps)" -ForegroundColor Cyan
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:29229/json/version" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) {
            Write-Host "    [ok] Chrome :29229" -ForegroundColor Green
        }
    }
    catch {
        Write-Host "    [!] Chrome CDP off - browser steps will fail; web UI still works" -ForegroundColor Yellow
    }
}

Write-Host "==> video-pipeline studio (no Telegram)" -ForegroundColor Cyan
Write-Host "    API: http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host "    UI:  cd web; npm run dev  -> http://localhost:3000" -ForegroundColor Yellow
Write-Host ""

$env:TELEGRAM_ENABLED = 'false'
& $venvPython -m app.main
