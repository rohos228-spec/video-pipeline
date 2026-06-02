# video-pipeline: обновление кода с GitHub + пересборка web
# Запуск из корня репо:
#   powershell -ExecutionPolicy Bypass -File .\UPDATE.ps1

[CmdletBinding()]
param(
    [switch]$SkipWeb,
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-OK($msg) { Write-Host "    [ok] $msg" -ForegroundColor Green }

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: запусти из корня video-pipeline." -ForegroundColor Red
    exit 1
}

if (-not $SkipPull) {
    Write-Step "git pull origin main"
    git fetch origin main
    git pull --ff-only origin main
    Write-OK "код обновлён"
}

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: нет .venv — сначала .\install.ps1" -ForegroundColor Red
    exit 1
}

Write-Step "pip install (editable)"
& $venvPython -m pip install -e ".[dev,whisper]" -q
Write-OK "python deps"

Get-ChildItem -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

if (-not $SkipWeb) {
    if (Test-Path "web\package.json") {
        Write-Step "npm ci + build (web)"
        Push-Location web
        npm ci --silent 2>$null
        if ($LASTEXITCODE -ne 0) { npm install --silent }
        npm run build
        Pop-Location
        Write-OK "web собран"
    }
}

Write-Host ""
Write-Host "Готово. Запуск:" -ForegroundColor Green
Write-Host "  Studio:  powershell -ExecutionPolicy Bypass -File .\start-studio.ps1" -ForegroundColor Yellow
Write-Host "  + UI:    cd web; npm run dev" -ForegroundColor Yellow
Write-Host "  Бот:     powershell -ExecutionPolicy Bypass -File .\start.ps1" -ForegroundColor Yellow
