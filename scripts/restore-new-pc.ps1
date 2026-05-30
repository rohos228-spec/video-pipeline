# Первичная настройка video-pipeline на новом ПК после распаковки бэкапа.
#
#   cd C:\Projects\video-pipeline
#   powershell -ExecutionPolicy Bypass -File scripts\restore-new-pc.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "==> restore-new-pc in $Root"

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example — edit TELEGRAM_BOT_TOKEN, paths if needed"
    } else {
        Write-Warning ".env missing — create manually"
    }
}

Write-Host "==> Python venv..."
python -m venv .venv
.\.venv\Scripts\pip.exe install -U pip
.\.venv\Scripts\pip.exe install -r requirements.txt

Write-Host "==> Whisper medium (optional preload)..."
.\.venv\Scripts\python.exe scripts\download_whisper.py medium

if (Test-Path "web\package.json") {
    Write-Host "==> Web UI build..."
    Push-Location web
    npm ci 2>$null; if ($LASTEXITCODE -ne 0) { npm install }
    npm run build
    Pop-Location
    if (Test-Path "BUILD-WEB.cmd") { & ".\BUILD-WEB.cmd" }
}

Write-Host ""
Write-Host "DONE. Next:"
Write-Host "  1. Install ffmpeg (in PATH)"
Write-Host "  2. Chrome CDP for outsee: see HOW_TO_RUN.md"
Write-Host "  3. .\run-backend.ps1"
Write-Host "  4. Browser http://127.0.0.1:8765"
Write-Host ""
Write-Host "Projects are in data\ — state.db should work as-is."
