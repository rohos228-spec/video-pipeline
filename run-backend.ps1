# Запуск бэкенда из корня репозитория
# powershell -ExecutionPolicy Bypass -File .\run-backend.ps1

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root

$logFile = Join-Path $Root "data\backend.log"
$port = 8765

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: pyproject.toml not found in $Root" -ForegroundColor Red
    exit 1
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: .venv not found. Run .\install.ps1 first (Launcher: 1 Full install)." -ForegroundColor Red
    exit 1
}

$logDir = Split-Path -Parent $logFile
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

function Test-Port8765InUse {
    $hits = netstat -ano 2>$null | Select-String ":$port\s" | Select-String "LISTENING"
    return [bool]$hits
}

function Show-Port8765Owners {
    netstat -ano 2>$null | Select-String ":$port\s" | Select-String "LISTENING" | ForEach-Object {
        Write-Host "  $_" -ForegroundColor DarkGray
    }
}

Write-Host "==> video-pipeline backend (cwd=$Root)" -ForegroundColor Cyan
Write-Host "    http://127.0.0.1:$port" -ForegroundColor Yellow
Write-Host "    log: data\backend.log" -ForegroundColor DarkGray
Write-Host ""

if (Test-Port8765InUse) {
    Write-Host "ERROR: port $port is already in use (old backend still running?)." -ForegroundColor Red
    Show-Port8765Owners
    Write-Host "Fix: Launcher -> 4 Stop, or close the other backend PowerShell window." -ForegroundColor Yellow
    Write-Host "     Then run this script again." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Press Enter to close..." -ForegroundColor Gray
    Read-Host | Out-Null
    exit 1
}

if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
    Write-Host "WARNING: web/out/index.html missing - API will work, UI needs: Launcher 6 Build Web UI" -ForegroundColor Yellow
}

Write-Host "Checking Python imports..." -ForegroundColor DarkGray
$importCheck = & $py -c "import app.main; print('imports ok')" 2>&1
$importCheck | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Python failed before startup. Try:" -ForegroundColor Red
    Write-Host "  .\install.ps1   or   Launcher -> 1 Full install" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -e `".[dev]`"" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Press Enter to close..." -ForegroundColor Gray
    Read-Host | Out-Null
    exit 1
}

$env:TELEGRAM_ENABLED = "false"
Write-Host ""
Write-Host ">>> DO NOT CLOSE THIS WINDOW while Studio is open <<<" -ForegroundColor Yellow
Write-Host "    Wait for: Uvicorn running on http://127.0.0.1:$port" -ForegroundColor Yellow
Write-Host ""

$transcriptOk = $false
try {
    Start-Transcript -Path $logFile -Append -ErrorAction Stop | Out-Null
    $transcriptOk = $true
} catch {
    Write-Host "Note: could not start transcript ($($_.Exception.Message)) — logging to console only." -ForegroundColor DarkYellow
    "$(Get-Date -Format o) transcript failed: $($_.Exception.Message)" | Add-Content -Path $logFile -Encoding UTF8
}

$exitCode = 0
try {
    & $py -m app.main
    if ($null -ne $LASTEXITCODE) { $exitCode = $LASTEXITCODE }
    elseif (-not $?) { $exitCode = 1 }
} finally {
    if ($transcriptOk) {
        try { Stop-Transcript | Out-Null } catch { }
    }
}

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Backend exited with code $exitCode" -ForegroundColor Red
    Write-Host "Log: data\backend.log  |  Diagnose: .\diagnose-backend.ps1" -ForegroundColor Red
    if (Test-Path $logFile) {
        Write-Host ""
        Write-Host "--- last lines of backend.log ---" -ForegroundColor DarkGray
        Get-Content $logFile -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    }
}
Write-Host ""
Write-Host "Press Enter to close this window..." -ForegroundColor Gray
Read-Host | Out-Null
