# Запуск бэкенда из корня репозитория
# RUN_BACKEND_ID=session-log-v3  (UTF-8 console + log)
# powershell -ExecutionPolicy Bypass -File .\run-backend.ps1

param(
    [switch]$NoPause
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root

# Loguru пишет UTF-8; без этого в консоли Windows кириллица = «каракули».
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
try { chcp 65001 | Out-Null } catch { }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

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
try {
    $gitHead = (git -C $Root rev-parse --short HEAD 2>$null).Trim()
    if ($gitHead) { Write-Host "    git HEAD: $gitHead" -ForegroundColor DarkGray }
} catch { }
$verFile = Join-Path $Root "web\STUDIO_VERSION"
if (Test-Path $verFile) {
    $vl = @(Get-Content -LiteralPath $verFile -Encoding UTF8 | Select-Object -First 2)
    $vb = $vl[0]
    $vs = if ($vl.Count -gt 1) { $vl[1] } else { "?" }
    Write-Host "    STUDIO_VERSION: v$vb  $vs" -ForegroundColor Green
}
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

Write-Host "Preflight: create_app() ..." -ForegroundColor Gray
$preflightOut = @(& $py -c "from app.web.api import create_app; create_app(); print('create_app OK')" 2>&1)
$preflightOk = ($LASTEXITCODE -eq 0) -and ($preflightOut -match "create_app OK")
if (-not $preflightOk) {
    Write-Host ""
    Write-Host "PREFLIGHT FAILED — backend will not listen on :8765" -ForegroundColor Red
    $preflightOut | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Usually fixed by: git pull  then run UPDATE-STUDIO.cmd" -ForegroundColor Yellow
    Write-Host "Need app/web/api.py with response_model=None (commit 5190d6c+)" -ForegroundColor Yellow
    Write-Host ""
    Write-BackendLogLine "PREFLIGHT FAILED: $($preflightOut -join ' | ')"
    Write-Host "Press Enter to close..." -ForegroundColor Gray
    Read-Host | Out-Null
    exit 1
}
Write-Host "Preflight OK" -ForegroundColor Green
Write-BackendLogLine "preflight create_app OK"

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
if (-not $NoPause) {
    Write-Host ""
    Write-Host "Press Enter to close..." -ForegroundColor Gray
    Read-Host | Out-Null
}
