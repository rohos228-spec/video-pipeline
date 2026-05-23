# video-pipeline: запуск бота на Windows
# Запуск:
#   powershell -ExecutionPolicy Bypass -File .\start.ps1
#
# Что делает:
#   1. Если Chrome с remote-debugging-port=29229 не запущен — стартует его.
#   2. Запускает python -m app.main (Telegram-бот + воркер).
#
# Ctrl+C для остановки.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-OK($msg) { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }

# ---------- 0. Проверка папки и venv ----------

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: запусти скрипт из корня репо video-pipeline." -ForegroundColor Red
    exit 1
}

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: venv не найден. Запусти .\install.ps1 сначала." -ForegroundColor Red
    exit 1
}

# ---------- 1. .env проверка ----------

if (-not (Test-Path ".env")) {
    Write-Host "ERROR: .env не найден. Запусти .\install.ps1 сначала." -ForegroundColor Red
    exit 1
}

$envText = Get-Content ".env" -Raw
$tgDisabled = $envText -match "(?m)^TELEGRAM_ENABLED\s*=\s*(false|0|no)\s*$"
$tokenLine = ($envText -split "`n" | Where-Object { $_ -match "^TELEGRAM_BOT_TOKEN=\s*\S" })
if (-not $tgDisabled -and -not $tokenLine) {
    Write-Host "ERROR: TELEGRAM_BOT_TOKEN не задан. Либо впиши токен, либо TELEGRAM_ENABLED=false и .\start-studio.ps1" -ForegroundColor Red
    exit 1
}
if ($tgDisabled) {
    Write-Warn "TELEGRAM_ENABLED=false — запускаю без бота (как start-studio.ps1)"
}

# ---------- 2. Chrome с remote-debugging-port=29229 ----------

Write-Step "Проверяю Chrome с remote-debugging-port=29229"

$cdpRunning = $false
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:29229/json/version" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        $cdpRunning = $true
    }
} catch {
    $cdpRunning = $false
}

if ($cdpRunning) {
    Write-OK "Chrome уже работает на 29229 — не трогаю"
} else {
    # Проверяем, есть ли вообще ЛЮБОЕ окно chrome.exe запущенное в системе
    # (включая обычное пользовательское). Если есть — не пытаемся открыть
    # ещё одно и не ждём CDP: либо юзер сам поднимет нужное окно с CDP,
    # либо бот свалится при первом обращении и юзер увидит понятную
    # ошибку. Это лучше чем тупо ждать 15 секунд впустую.
    $chromeRunning = @(Get-Process -Name chrome -ErrorAction SilentlyContinue).Count -gt 0
    if ($chromeRunning) {
        Write-Warn "Chrome уже запущен (но без remote-debugging-port=29229)."
        Write-Warn "Не открываю новое окно и не жду — запускаю бота сразу."
        Write-Warn "Если бот упадёт с CDP-ошибкой — закрой ВСЕ окна Chrome и перезапусти .\start.ps1"
    } else {
        $chromePaths = @(
            "C:\Program Files\Google\Chrome\Application\chrome.exe",
            "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
        )
        $chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $chrome) {
            Write-Host "ERROR: Chrome не найден в стандартных путях. Установи Chrome." -ForegroundColor Red
            exit 1
        }
        $userDataDir = "$env:USERPROFILE\.vp_browser_data"
        Write-Host ""
        Write-Host "    Запускаю отдельный Chrome для бота." -ForegroundColor Yellow
        Write-Host "    Залогинься в нём при первом запуске:" -ForegroundColor Yellow
        Write-Host "      - https://chatgpt.com/" -ForegroundColor Yellow
        Write-Host "      - https://outsee.io/" -ForegroundColor Yellow
        Write-Host "    Этот Chrome должен быть открыт всё время, пока работает бот." -ForegroundColor Yellow
        Write-Host ""
        Start-Process -FilePath $chrome -ArgumentList @(
            "--remote-debugging-port=29229",
            "--user-data-dir=$userDataDir"
        )
        # Ждём пока CDP-эндпоинт ответит
        $maxWait = 15
        $waited = 0
        while ($waited -lt $maxWait) {
            Start-Sleep -Seconds 1
            $waited++
            try {
                $resp = Invoke-WebRequest -Uri "http://localhost:29229/json/version" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
                if ($resp.StatusCode -eq 200) {
                    Write-OK "Chrome поднялся на 29229 (за $waited сек)"
                    break
                }
            } catch { }
        }
        if ($waited -ge $maxWait) {
            Write-Warn "Chrome не ответил по http://localhost:29229/json/version за $maxWait сек. Запускаю бота всё равно — если он упадёт с CDP-ошибкой, проверь Chrome руками."
        }
    }
}

# ---------- 3. Запуск бота ----------

Write-Step "Запускаю video-pipeline (python -m app.main)"
Write-Host "    Ctrl+C для остановки." -ForegroundColor Yellow
Write-Host ""

& $venvPython -m app.main
