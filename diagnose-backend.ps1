# Диагностика: почему не поднимается бэкенд на Windows
# powershell -ExecutionPolicy Bypass -File .\diagnose-backend.ps1

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root
$port = 8765

function Write-Ok($msg) { Write-Host "[OK]  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "=== Video Pipeline — backend diagnose ===" -ForegroundColor Cyan
Write-Host "Folder: $Root"
Write-Host ""

if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Write-Fail "Not a video-pipeline repo root (no pyproject.toml)"
    exit 1
}
Write-Ok "pyproject.toml found"

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Fail ".venv missing — run: .\install.ps1  (Launcher: 1 Full install)"
} else {
    Write-Ok ".venv python: $py"
    $ver = & $py -c "import sys; print(sys.version)" 2>&1
    Write-Host "      $ver" -ForegroundColor DarkGray
}

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Warn ".env missing — install.ps1 copies from .env.example"
} else {
    Write-Ok ".env exists"
}

$ui = Join-Path $Root "web\out\index.html"
if (Test-Path $ui) {
    Write-Ok "web/out built (Studio UI)"
} else {
    Write-Warn "web/out/index.html missing — API works, UI needs: cd web; npm install; npm run build"
}

Write-Host ""
Write-Host "--- Git ---" -ForegroundColor Cyan
try {
    $branch = git -C $Root rev-parse --abbrev-ref HEAD 2>$null
    $head = git -C $Root rev-parse --short HEAD 2>$null
    Write-Host "branch: $branch  commit: $head"
} catch {
    Write-Warn "git info unavailable"
}

Write-Host ""
Write-Host "--- Port $port ---" -ForegroundColor Cyan
$listen = netstat -ano 2>$null | Select-String ":$port\s" | Select-String "LISTENING"
if ($listen) {
    Write-Ok "something is listening on :$port"
    $listen | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
} else {
    Write-Warn "port $port not listening — backend not running"
}

Write-Host ""
Write-Host "--- HTTP ---" -ForegroundColor Cyan
try {
    $health = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 3
    Write-Ok "/api/health -> $($health.status)"
    try {
        $sv = Invoke-RestMethod "http://127.0.0.1:$port/api/studio-version" -TimeoutSec 3
        Write-Host "      studio: $($sv.label)  pipeline_ok=$($sv.pipeline_ok)" -ForegroundColor DarkGray
    } catch {
        Write-Warn "studio-version unreachable"
    }
} catch {
    Write-Fail "http://127.0.0.1:$port not reachable"
    Write-Host "      Start: .\run-backend.ps1  or Launcher -> 2 Start Studio" -ForegroundColor DarkGray
}

if (Test-Path $py) {
    Write-Host ""
    Write-Host "--- Python import test ---" -ForegroundColor Cyan
    $out = & $py -c "import app.main; print('import app.main OK')" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok ($out -join " ")
    } else {
        Write-Fail "import app.main failed"
        $out | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        Write-Host "      Fix: .\.venv\Scripts\python.exe -m pip install -e `".[dev]`"" -ForegroundColor Yellow
    }
}

$logFile = Join-Path $Root "data\backend.log"
Write-Host ""
Write-Host "--- backend.log (last 20 lines) ---" -ForegroundColor Cyan
if (Test-Path $logFile) {
    Get-Content $logFile -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
} else {
    Write-Warn "no data\backend.log yet — run .\run-backend.ps1 once"
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. git pull origin devin/windows-installer"
Write-Host "  2. Launcher -> 5 Update all  (or pip install + npm run build)"
Write-Host "  3. Launcher -> 4 Stop  then  2 Start Studio"
Write-Host "  4. Open http://127.0.0.1:8765  (not :3000)"
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host | Out-Null
