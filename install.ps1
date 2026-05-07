# video-pipeline: установка на Windows без Docker
# Запуск (из папки video-pipeline):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Что делает:
#   1. Проверяет/ставит Python 3.11 и FFmpeg через winget.
#   2. Создаёт venv .venv и ставит зависимости из pyproject.toml.
#   3. Создаёт .env из .env.example, спрашивает Telegram bot token.
#   4. Создаёт папку data/ под SQLite и артефакты.
#
# После установки запускай: .\start.ps1

[CmdletBinding()]
param(
    [string]$BotToken = "",
    [string]$TelegramProxyUrl = "socks5://vhGfB2:0tnzqA@45.130.61.143:8000",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$PSStyle.OutputRendering = "Ansi"

function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "    [ok] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "    [!] $msg" -ForegroundColor Yellow
}

function Have-Cmd($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

function Find-Python311 {
    # 1. Команда `py -3.11`
    if (Have-Cmd py) {
        $v = & py -3.11 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($v -eq "3.11" -or $v -eq "3.12") {
            return @{ Cmd = "py"; Args = @("-3.11") }
        }
    }
    # 2. Команда `python` если 3.11 / 3.12
    if (Have-Cmd python) {
        $v = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($v -eq "3.11" -or $v -eq "3.12") {
            return @{ Cmd = "python"; Args = @() }
        }
    }
    return $null
}

# ---------- 0. Проверка папки ----------

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: запусти скрипт из корня репо video-pipeline (там должен быть pyproject.toml)." -ForegroundColor Red
    exit 1
}

# ---------- 1. winget ----------

Write-Step "Проверяю winget"
if (-not (Have-Cmd winget)) {
    Write-Host "ERROR: winget не найден. Поставь 'App Installer' из Microsoft Store, перезапусти PowerShell и запусти install.ps1 снова." -ForegroundColor Red
    Write-Host "       https://apps.microsoft.com/detail/9NBLGGH4NNS1" -ForegroundColor Red
    exit 1
}
Write-OK "winget есть"

# ---------- 2. Python 3.11 ----------

Write-Step "Проверяю Python 3.11/3.12"
$py = Find-Python311
if (-not $py) {
    Write-Warn "Python 3.11 не найден — ставлю через winget"
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    $py = Find-Python311
    if (-not $py) {
        Write-Host "ERROR: после установки Python всё ещё не найден. Закрой PowerShell, открой новый и запусти install.ps1 снова." -ForegroundColor Red
        exit 1
    }
}
$pyVersion = & $py.Cmd @($py.Args) -c "import sys; print(sys.version)" 2>$null
Write-OK "Python: $pyVersion"

# ---------- 3. FFmpeg ----------

Write-Step "Проверяю FFmpeg"
if (-not (Have-Cmd ffmpeg)) {
    Write-Warn "FFmpeg не найден — ставлю через winget"
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    if (-not (Have-Cmd ffmpeg)) {
        Write-Warn "ffmpeg всё ещё не в PATH. Закрой PowerShell, открой новый и проверь: ffmpeg -version"
        Write-Warn "Если не помогло — добавь C:\Users\<user>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.*\bin в PATH вручную."
    } else {
        Write-OK "FFmpeg установлен"
    }
} else {
    Write-OK "FFmpeg есть"
}

# ---------- 4. Chrome ----------

Write-Step "Проверяю Chrome"
$chromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Warn "Chrome не найден. Скачай и установи: https://www.google.com/chrome/"
    Write-Warn "Без Chrome бот работать не будет — он подключается к нему по CDP."
} else {
    Write-OK "Chrome: $chrome"
}

# ---------- 5. venv + зависимости ----------

Write-Step "Создаю virtualenv .venv (Python 3.11/3.12)"
if (-not (Test-Path ".venv")) {
    & $py.Cmd @($py.Args) -m venv .venv
}
$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: venv не создался ($venvPython)" -ForegroundColor Red
    exit 1
}
Write-OK "venv создан"

Write-Step "Обновляю pip и ставлю зависимости (~1 GB, может занять 5-10 минут)"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install -e . упал. Скопируй вывод выше и пришли." -ForegroundColor Red
    exit 1
}
Write-OK "Зависимости установлены"

# ---------- 6. .env ----------

Write-Step "Настраиваю .env"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-OK ".env создан из .env.example"
}

$envContent = Get-Content ".env" -Raw

# Telegram bot token
$tokenLine = ($envContent -split "`n" | Where-Object { $_ -match "^TELEGRAM_BOT_TOKEN=" })
if (-not $tokenLine -or $tokenLine -match "^TELEGRAM_BOT_TOKEN=\s*$") {
    if ($BotToken) {
        $envContent = $envContent -replace "(?m)^TELEGRAM_BOT_TOKEN=.*$", "TELEGRAM_BOT_TOKEN=$BotToken"
        Write-OK "TELEGRAM_BOT_TOKEN записан из параметра"
    } elseif (-not $NonInteractive) {
        Write-Host ""
        Write-Host "    Нужен токен Telegram-бота от @BotFather." -ForegroundColor Yellow
        Write-Host "    Если есть бот @content1400_bot — открой Telegram → @BotFather → /mybots → выбери его → API Token." -ForegroundColor Yellow
        Write-Host "    Если нет — напиши @BotFather, /newbot, придумай имя/username, получи токен." -ForegroundColor Yellow
        $token = Read-Host "    Вставь токен (или Enter чтобы пропустить и заполнить вручную позже)"
        if ($token) {
            $envContent = $envContent -replace "(?m)^TELEGRAM_BOT_TOKEN=.*$", "TELEGRAM_BOT_TOKEN=$token"
            Write-OK "TELEGRAM_BOT_TOKEN записан"
        } else {
            Write-Warn "TELEGRAM_BOT_TOKEN пуст. Открой .env и впиши вручную перед запуском бота."
        }
    }
} else {
    Write-OK "TELEGRAM_BOT_TOKEN уже задан"
}

# Telegram proxy
$proxyLine = ($envContent -split "`n" | Where-Object { $_ -match "^TELEGRAM_PROXY_URL=" })
if (-not $proxyLine -or $proxyLine -match "^TELEGRAM_PROXY_URL=\s*$") {
    if ($TelegramProxyUrl) {
        $envContent = $envContent -replace "(?m)^TELEGRAM_PROXY_URL=.*$", "TELEGRAM_PROXY_URL=$TelegramProxyUrl"
        Write-OK "TELEGRAM_PROXY_URL установлен (SOCKS5)"
    }
}

Set-Content -Path ".env" -Value $envContent -NoNewline -Encoding UTF8

# ---------- 7. data/ ----------

Write-Step "Создаю папку data/"
New-Item -ItemType Directory -Force -Path "data" | Out-Null
Write-OK "data/ готова"

# ---------- Done ----------

Write-Host ""
Write-Host "===================================" -ForegroundColor Green
Write-Host "  Установка завершена!" -ForegroundColor Green
Write-Host "===================================" -ForegroundColor Green
Write-Host ""
Write-Host "Дальше:" -ForegroundColor Cyan
Write-Host "  1. Если не вписал TELEGRAM_BOT_TOKEN — открой .env и впиши его."
Write-Host "  2. Запусти бота:" -ForegroundColor Cyan
Write-Host "       .\start.ps1" -ForegroundColor White
Write-Host "     Скрипт стартует Chrome (с remote-debugging-port=29229) и запустит бота."
Write-Host "     При первом запуске залогинься в открывшемся Chrome:"
Write-Host "       - https://chatgpt.com/"
Write-Host "       - https://outsee.io/"
Write-Host "  3. В Telegram отправь боту /start, потом /new <тема>."
Write-Host ""
