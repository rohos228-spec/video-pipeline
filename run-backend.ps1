# Запуск бэкенда из корня репозитория
# RUN_BACKEND_ID=session-log-v2  (не падает если backend.log занят)
# powershell -ExecutionPolicy Bypass -File .\run-backend.ps1

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root

$logDir = Join-Path $Root "data"
$sharedLog = Join-Path $logDir "backend.log"
$sessionLog = Join-Path $logDir "backend-$PID.log"

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: pyproject.toml not found in $Root" -ForegroundColor Red
    exit 1
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: .venv not found. Run .\install.ps1 first." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

function Write-BackendLogLine([string]$Line) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "$ts  $Line"
    try {
        Add-Content -Path $sessionLog -Value $entry -Encoding UTF8 -ErrorAction Stop
    } catch { }
    try {
        Add-Content -Path $sharedLog -Value $entry -Encoding UTF8 -ErrorAction Stop
    } catch {
        # Второй бэкенд или Notepad держит backend.log — пишем только в session log
    }
}

Write-Host "==> video-pipeline backend (cwd=$Root)" -ForegroundColor Cyan
Write-Host "    http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host "    log (this run): data\backend-$PID.log" -ForegroundColor DarkGray
Write-Host "    log (shared):   data\backend.log (may be locked if 2 backends)" -ForegroundColor DarkGray
Write-Host ""

try {
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop
    if ($listener) {
        Write-Host "WARNING: port 8765 already in use (PID $($listener.OwningProcess))." -ForegroundColor Yellow
        Write-Host "         Close the other backend window or run: .\stop-backend.cmd" -ForegroundColor Yellow
        Write-Host ""
    }
} catch { }

if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
    Write-Host "WARNING: web/out/index.html missing - Launcher button 6 Build Web UI" -ForegroundColor Yellow
}

$env:TELEGRAM_ENABLED = "false"
$env:WEB_HOST = "127.0.0.1"
$env:WEB_PORT = "8765"

Write-BackendLogLine "=== backend start PID=$PID ==="

Write-Host ""
Write-Host ">>> DO NOT CLOSE THIS WINDOW while Studio is open <<<" -ForegroundColor Yellow
Write-Host "    Wait for: Uvicorn running on http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host ""

$exitCode = 0
try {
    & $py -m app.main 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        Write-BackendLogLine $line
    }
    if ($null -ne $LASTEXITCODE) {
        $exitCode = $LASTEXITCODE
    }
} catch {
    $msg = $_.Exception.Message
    Write-Host "Backend crashed: $msg" -ForegroundColor Red
    Write-BackendLogLine "CRASH: $msg"
    $exitCode = 1
}

Write-BackendLogLine "=== backend exit code=$exitCode ==="

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Backend exited with code $exitCode" -ForegroundColor Red
    Write-Host "See data\backend-$PID.log (and data\backend.log if not locked)" -ForegroundColor Red
}
Write-Host ""
Write-Host "Press Enter to close..." -ForegroundColor Gray
Read-Host | Out-Null
