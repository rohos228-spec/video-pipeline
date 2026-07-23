# Запуск бэкенда из корня репозитория (вызывается из scripts/studio.ps1)
# RUN_BACKEND_ID=session-log-v3

param(
    [switch]$NoPause
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
try { chcp 65001 | Out-Null } catch { }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$logDir = Join-Path $Root "data"
$sharedLog = Join-Path $logDir "backend.log"
$sessionLog = Join-Path $logDir "backend-$PID.log"

if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Write-Host "ОШИБКА: pyproject.toml не найден в $Root" -ForegroundColor Red
    exit 1
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ОШИБКА: .venv не найден. Сначала запустите install.ps1 или STUDIO.cmd -> [3] Починить установку." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

function Write-BackendLogLine([string]$Line) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "$ts  $Line"
    try { Add-Content -Path $sessionLog -Value $entry -Encoding UTF8 -ErrorAction Stop } catch { }
    try { Add-Content -Path $sharedLog -Value $entry -Encoding UTF8 -ErrorAction Stop } catch { }
}

Write-Host "==> video-pipeline backend (cwd=$Root)" -ForegroundColor Cyan
Write-Host "    http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host "    лог (этот запуск): data\backend-$PID.log" -ForegroundColor DarkGray
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
        Write-Host "ВНИМАНИЕ: порт 8765 занят (PID $($listener.OwningProcess))." -ForegroundColor Yellow
        Write-Host "         Закройте другое окно бэкенда или: stop-backend.cmd" -ForegroundColor Yellow
        Write-Host ""
    }
} catch { }

if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
    Write-Host "ВНИМАНИЕ: web/out/index.html отсутствует — STUDIO.cmd -> [3] Починить установку" -ForegroundColor Yellow
}

$env:TELEGRAM_ENABLED = "false"
$env:WEB_HOST = "127.0.0.1"
$env:WEB_PORT = "8765"
$env:HF_HUB_DISABLE_SYMLINKS = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "0"

# NeMo/HF: до любого python — иначе WinError 32 в %TEMP% (AppData\Local\Temp)
$cacheRoot = Join-Path $Root "data\.cache"
$cacheTemp = Join-Path $cacheRoot "temp"
$cacheHf = Join-Path $cacheRoot "huggingface"
$cacheHfHub = Join-Path $cacheHf "hub"
$cacheNemo = Join-Path $cacheRoot "nemo"
foreach ($d in @($cacheRoot, $cacheTemp, $cacheHf, $cacheHfHub, $cacheNemo)) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
}
$env:TEMP = $cacheTemp
$env:TMP = $cacheTemp
$env:TMPDIR = $cacheTemp
$env:HF_HOME = $cacheHf
$env:HUGGINGFACE_HUB_CACHE = $cacheHfHub
$env:TRANSFORMERS_CACHE = $cacheHfHub
$env:NEMO_CACHE_DIR = $cacheNemo
$env:HF_HUB_ENABLE_HF_TRANSFER = "0"
$env:TOKENIZERS_PARALLELISM = "false"

Write-BackendLogLine "=== backend start PID=$PID ==="

Write-Host "Проверка create_app() ..." -ForegroundColor Gray
$preflightOut = @(& $py -c "from app.web.api import create_app; create_app(); print('create_app OK')" 2>&1)
$preflightOk = ($LASTEXITCODE -eq 0) -and ($preflightOut -match "create_app OK")
if (-not $preflightOk) {
    Write-Host ""
    Write-Host "ПРОВЕРКА НЕ ПРОШЛА — бэкенд не поднимется на :8765" -ForegroundColor Red
    $preflightOut | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Обычно помогает: STUDIO.cmd -> [2] Обновить и запустить" -ForegroundColor Yellow
    Write-BackendLogLine "PREFLIGHT FAILED: $($preflightOut -join ' | ')"
    if (-not $NoPause) {
        Write-Host "Нажмите Enter для закрытия..." -ForegroundColor Gray
        Read-Host | Out-Null
    }
    exit 1
}
Write-Host "Проверка OK" -ForegroundColor Green
Write-BackendLogLine "preflight create_app OK"

Write-Host ""
Write-Host ">>> НЕ ЗАКРЫВАЙТЕ ЭТО ОКНО, пока открыта студия <<<" -ForegroundColor Yellow
Write-Host "    Дождитесь: Uvicorn running on http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host ""

$exitCode = 0
try {
    & $py -m app.main 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        Write-BackendLogLine $line
    }
    if ($null -ne $LASTEXITCODE) { $exitCode = $LASTEXITCODE }
} catch {
    $msg = $_.Exception.Message
    Write-Host "Бэкенд упал: $msg" -ForegroundColor Red
    Write-BackendLogLine "CRASH: $msg"
    $exitCode = 1
}

Write-BackendLogLine "=== backend exit code=$exitCode ==="
if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Бэкенд завершился с кодом $exitCode" -ForegroundColor Red
    Write-Host "См. data\backend-$PID.log" -ForegroundColor Red
}
if (-not $NoPause) {
    Write-Host ""
    Write-Host "Нажмите Enter для закрытия..." -ForegroundColor Gray
    Read-Host | Out-Null
}
