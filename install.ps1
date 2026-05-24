# video-pipeline: установка на Windows без Docker
# Запуск (из папки video-pipeline):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

[CmdletBinding()]
param(
    [string]$BotToken = "",
    [string]$TelegramProxyUrl = "socks5://vhGfB2:0tnzqA@45.130.61.143:8000",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

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
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

# Возвращает строку: либо "py -3.11", либо "py -3.12", либо "python", либо $null.
# Все вызовы интерпретатора обернуты в try/catch + временное снятие
# ErrorActionPreference, иначе stderr из py.exe (когда нужная версия не
# установлена) под Stop-режимом превращается в фатальную ошибку и убивает скрипт.
function Find-PythonCmd {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if (Have-Cmd py) {
            foreach ($ver in @("3.11", "3.12")) {
                try {
                    $check = & py "-$ver" -c "print(1)" 2>$null
                    if ($LASTEXITCODE -eq 0 -and $check -eq "1") {
                        return "py -$ver"
                    }
                } catch { }
            }
        }
        if (Have-Cmd python) {
            try {
                $vraw = & python -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>$null
                if ($LASTEXITCODE -eq 0 -and ($vraw -eq "3.11" -or $vraw -eq "3.12")) {
                    return "python"
                }
            } catch { }
        }
        return $null
    }
    finally {
        $ErrorActionPreference = $prevEAP
    }
}

function Invoke-Python($pyCmd, [string[]]$Arguments) {
    $parts = $pyCmd -split ' '
    $exe = $parts[0]
    $preArgs = @()
    if ($parts.Length -gt 1) { $preArgs = $parts[1..($parts.Length - 1)] }
    $allArgs = $preArgs + $Arguments
    & $exe @allArgs
}

# ---------- 0. Проверка папки ----------

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: запусти скрипт из корня репо video-pipeline (там должен быть pyproject.toml)." -ForegroundColor Red
    exit 1
}

# ---------- 1. winget ----------

Write-Step "Проверяю winget"
if (-not (Have-Cmd winget)) {
    Write-Host "ERROR: winget не найден. Поставь App Installer из Microsoft Store, перезапусти PowerShell и запусти install.ps1 снова." -ForegroundColor Red
    Write-Host "       https://apps.microsoft.com/detail/9NBLGGH4NNS1" -ForegroundColor Red
    exit 1
}
Write-OK "winget есть"

# ---------- 2. Python 3.11/3.12 ----------

Write-Step "Проверяю Python 3.11/3.12"
$pyCmd = Find-PythonCmd
if ([string]::IsNullOrEmpty($pyCmd)) {
    Write-Warn "Python 3.11 не найден — ставлю через winget"
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    $pyCmd = Find-PythonCmd
    if ([string]::IsNullOrEmpty($pyCmd)) {
        Write-Host "ERROR: Python всё ещё не найден. Закрой PowerShell, открой новый и запусти install.ps1 снова." -ForegroundColor Red
        exit 1
    }
}
$pyVersion = Invoke-Python $pyCmd @("-c", "import sys; print(sys.version)")
Write-OK "Python ($pyCmd): $pyVersion"

# ---------- 3. FFmpeg ----------

Write-Step "Проверяю FFmpeg"
if (-not (Have-Cmd ffmpeg)) {
    Write-Warn "FFmpeg не найден — ставлю через winget"
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    if (-not (Have-Cmd ffmpeg)) {
        Write-Warn "ffmpeg всё ещё не в PATH. Закрой PowerShell, открой новый и проверь: ffmpeg -version"
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
if ($null -eq $chrome) {
    Write-Warn "Chrome не найден. Скачай и установи: https://www.google.com/chrome/"
    Write-Warn "Без Chrome бот работать не будет — он подключается к нему по CDP."
} else {
    Write-OK "Chrome: $chrome"
}

# ---------- 5. venv + зависимости ----------

Write-Step "Создаю virtualenv .venv"
if (-not (Test-Path ".venv")) {
    Invoke-Python $pyCmd @("-m", "venv", ".venv")
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

$envLines = Get-Content ".env"
$hasToken = $false
$hasProxy = $false
$tokenIdx = -1
$proxyIdx = -1
for ($i = 0; $i -lt $envLines.Length; $i++) {
    if ($envLines[$i] -match "^TELEGRAM_BOT_TOKEN=") {
        $tokenIdx = $i
        if ($envLines[$i] -notmatch "^TELEGRAM_BOT_TOKEN=\s*$") { $hasToken = $true }
    }
    if ($envLines[$i] -match "^TELEGRAM_PROXY_URL=") {
        $proxyIdx = $i
        if ($envLines[$i] -notmatch "^TELEGRAM_PROXY_URL=\s*$") { $hasProxy = $true }
    }
}

# Telegram bot token
if (-not $hasToken) {
    if ($BotToken) {
        if ($tokenIdx -ge 0) {
            $envLines[$tokenIdx] = "TELEGRAM_BOT_TOKEN=$BotToken"
        } else {
            $envLines += "TELEGRAM_BOT_TOKEN=$BotToken"
        }
        Write-OK "TELEGRAM_BOT_TOKEN записан из параметра"
    } elseif (-not $NonInteractive) {
        Write-Host ""
        Write-Host "    Нужен токен Telegram-бота от @BotFather." -ForegroundColor Yellow
        Write-Host "    Если есть бот @content1400_bot — открой Telegram, @BotFather, /mybots, выбери его, API Token." -ForegroundColor Yellow
        Write-Host "    Если нет — напиши @BotFather, /newbot, придумай имя/username, получи токен." -ForegroundColor Yellow
        $token = Read-Host "    Вставь токен (Enter чтобы пропустить и заполнить .env вручную)"
        if ($token) {
            if ($tokenIdx -ge 0) {
                $envLines[$tokenIdx] = "TELEGRAM_BOT_TOKEN=$token"
            } else {
                $envLines += "TELEGRAM_BOT_TOKEN=$token"
            }
            Write-OK "TELEGRAM_BOT_TOKEN записан"
        } else {
            if ($envLines -notmatch "TELEGRAM_ENABLED") {
                $envLines += "TELEGRAM_ENABLED=false"
            } else {
                for ($i = 0; $i -lt $envLines.Count; $i++) {
                    if ($envLines[$i] -match "^TELEGRAM_ENABLED=") {
                        $envLines[$i] = "TELEGRAM_ENABLED=false"
                    }
                }
            }
            Write-Warn "TELEGRAM_BOT_TOKEN пуст — web-only (.\start-studio.ps1)."
        }
    }
} else {
    Write-OK "TELEGRAM_BOT_TOKEN уже задан"
}

# Telegram proxy
if (-not $hasProxy -and $TelegramProxyUrl) {
    if ($proxyIdx -ge 0) {
        $envLines[$proxyIdx] = "TELEGRAM_PROXY_URL=$TelegramProxyUrl"
    } else {
        $envLines += "TELEGRAM_PROXY_URL=$TelegramProxyUrl"
    }
    Write-OK "TELEGRAM_PROXY_URL установлен (SOCKS5)"
}

Set-Content -Path ".env" -Value $envLines -Encoding UTF8

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
Write-Host "Дальше (веб-студия без Telegram — по умолчанию):" -ForegroundColor Cyan
Write-Host "  1. Окно 1 — бэкенд:" -ForegroundColor Cyan
Write-Host "       .\run-backend.ps1" -ForegroundColor White
Write-Host "     (или .\start-studio.ps1 — то же самое с проверкой Chrome)"
Write-Host "  2. Окно 2 — UI:" -ForegroundColor Cyan
Write-Host "       cd web" -ForegroundColor White
Write-Host "       npm install" -ForegroundColor White
Write-Host "       npm run dev" -ForegroundColor White
Write-Host "     Браузер: http://localhost:3000"
Write-Host "  3. Chrome CDP :29229 — только для шагов ChatGPT/outsee (см. HOW_TO_RUN.md)."
Write-Host ""
Write-Host "С Telegram-ботом:" -ForegroundColor DarkGray
Write-Host "  TELEGRAM_ENABLED=true + токен в .env, затем .\start.ps1"
Write-Host ""
Write-Host "GUI-лаунчер (15 кнопок, без ручного ввода в PS):" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File .\installer\VideoPipelineLauncher.ps1" -ForegroundColor White
Write-Host ""
