# Video Pipeline Studio: Мастер быстрой установки на Windows
# Запуск: через SETUP.cmd или powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

[CmdletBinding()]
param(
    [string]$SourceEnvPath = "",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

try { chcp 65001 | Out-Null } catch { }
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "    [!] $msg" -ForegroundColor Yellow
}

function Write-Err([string]$msg) {
    Write-Host "    [ОШИБКА] $msg" -ForegroundColor Red
}

function Have-Cmd([string]$name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    if ($machine -or $user) {
        $env:Path = "$machine;$user"
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "         УСТАНОВКА VIDEO PIPELINE STUDIO                   " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Папка проекта: $Root" -ForegroundColor DarkGray
Write-Host "  Пользователь:  $env:USERNAME" -ForegroundColor DarkGray
Write-Host ""

# -------------------------------------------------------------
# ШАГ 1: Проверка и подключение .env файла
# -------------------------------------------------------------
Write-Step "Шаг 1/6: Проверка файла конфигурации (.env)"
$targetEnv = Join-Path $Root ".env"

if (Test-Path -LiteralPath $targetEnv) {
    Write-OK "Файл .env уже найден в папке проекта (содержимое не менялось)."
} else {
    $foundSource = $null

    if ($SourceEnvPath -and (Test-Path -LiteralPath $SourceEnvPath)) {
        $foundSource = $SourceEnvPath
    }

    if (-not $foundSource) {
        $candidates = @(
            (Join-Path (Split-Path -Parent $Root) ".env"),
            (Join-Path $env:USERPROFILE ".env"),
            (Join-Path (Join-Path $env:USERPROFILE "Downloads") ".env"),
            (Join-Path (Join-Path $env:USERPROFILE "Desktop") ".env")
        )
        foreach ($c in $candidates) {
            if (Test-Path -LiteralPath $c) {
                $foundSource = $c
                break
            }
        }
    }

    if ($foundSource) {
        Write-Host "    Найден файл .env: $foundSource" -ForegroundColor Yellow
        Copy-Item -LiteralPath $foundSource -Destination $targetEnv -Force
        Write-OK "Файл .env скопирован в проект из $foundSource"
    } elseif ($NonInteractive) {
        Write-Warn "Режим NonInteractive: .env отсутствует. Создаю из .env.example..."
        if (Test-Path (Join-Path $Root ".env.example")) {
            Copy-Item (Join-Path $Root ".env.example") $targetEnv
        }
    } else {
        Write-Warn "Файл .env не найден в папке проекта!"
        Write-Host ""
        Write-Host "  Для работы Студии нужен файл .env с вашими API-ключами." -ForegroundColor Yellow
        Write-Host "  Вы можете:" -ForegroundColor White
        Write-Host "    1. Перетащить файл .env прямо в это окно мышкой и нажать Enter." -ForegroundColor White
        Write-Host "    2. Или скопировать .env в папку: $Root" -ForegroundColor White
        Write-Host "       и просто нажать Enter." -ForegroundColor White
        Write-Host ""

        while (-not (Test-Path -LiteralPath $targetEnv)) {
            $inputPath = Read-Host "  Перетащите .env сюда или нажмите Enter после копирования"
            $cleaned = $inputPath.Trim().Trim('"').Trim("'")
            if ($cleaned -and (Test-Path -LiteralPath $cleaned)) {
                Copy-Item -LiteralPath $cleaned -Destination $targetEnv -Force
                Write-OK "Файл .env успешно скопирован в проект!"
                break
            }
            if (Test-Path -LiteralPath $targetEnv) {
                Write-OK "Файл .env обнаружен в папке проекта!"
                break
            }
            Write-Warn "Файл всё ещё не найден. Скопируйте .env в $Root и нажмите Enter."
        }
    }
}

# -------------------------------------------------------------
# ШАГ 2: Проверка и поиск Python (3.11 / 3.12)
# -------------------------------------------------------------
Write-Step "Шаг 2/6: Проверка Python 3.11/3.12"

function Find-LocalPython {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if (Have-Cmd py) {
            foreach ($ver in @("3.11", "3.12")) {
                try {
                    $check = & py "-$ver" -c "print(1)" 2>$null
                    if ($LASTEXITCODE -eq 0 -and "$check".Trim() -eq "1") {
                        return "py -$ver"
                    }
                } catch { }
            }
        }
        if (Have-Cmd python) {
            try {
                $vraw = & python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
                if ($LASTEXITCODE -eq 0 -and ($vraw -match "3\.11|3\.12")) {
                    return "python"
                }
            } catch { }
        }
        return $null
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

$pyCmd = Find-LocalPython

if (-not $pyCmd) {
    Write-Warn "Python 3.11 не найден в системе. Начинаю тихую установку..."
    $installed = $false

    if (Have-Cmd winget) {
        Write-Host "    Установка через winget (Python.Python.3.11)..." -ForegroundColor DarkGray
        try {
            & winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements --silent 2>&1 | Out-Null
            Refresh-Path
            $pyCmd = Find-LocalPython
            if ($pyCmd) { $installed = $true }
        } catch { }
    }

    if (-not $installed) {
        Write-Host "    Скачиваю официальный установщик Python 3.11.9..." -ForegroundColor DarkGray
        $installerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
        $installerPath = Join-Path $env:TEMP "python-3.11.9-installer.exe"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
            Write-Host "    Запуск тихой установки Python..." -ForegroundColor DarkGray
            $proc = Start-Process -FilePath $installerPath -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0" -Wait -PassThru
            Refresh-Path
            Start-Sleep -Seconds 2
            $pyCmd = Find-LocalPython
            if ($pyCmd) { $installed = $true }
        } catch {
            Write-Warn "Не удалось автоматически скачать Python: $($_.Exception.Message)"
        }
    }

    if (-not $pyCmd) {
        Write-Err "Не удалось установить Python 3.11 автоматически."
        Write-Host "  Пожалуйста, скачайте и установите Python 3.11 с официального сайта:" -ForegroundColor Yellow
        Write-Host "  https://www.python.org/downloads/release/python-3119/" -ForegroundColor Yellow
        Write-Host "  (ВАЖНО: поставьте галочку 'Add python.exe to PATH' при установке!)" -ForegroundColor Yellow
        exit 1
    }
}

Write-OK "Найден Python: $pyCmd"

# -------------------------------------------------------------
# ШАГ 3: Проверка FFmpeg
# -------------------------------------------------------------
Write-Step "Шаг 3/6: Проверка FFmpeg (видео- и аудиомонтаж)"

if (Have-Cmd ffmpeg) {
    Write-OK "FFmpeg уже установлен и доступен в PATH."
} else {
    Write-Warn "FFmpeg не найден. Устанавливаю..."
    $ffmpegInstalled = $false

    if (Have-Cmd winget) {
        Write-Host "    Установка через winget (Gyan.FFmpeg)..." -ForegroundColor DarkGray
        try {
            & winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent 2>&1 | Out-Null
            Refresh-Path
            if (Have-Cmd ffmpeg) { $ffmpegInstalled = $true }
        } catch { }
    }

    if (-not $ffmpegInstalled) {
        Write-Host "    Скачиваю портативный FFmpeg в tools\ffmpeg\..." -ForegroundColor DarkGray
        $toolsDir = Join-Path $Root "tools\ffmpeg"
        if (-not (Test-Path $toolsDir)) { New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null }
        $zipPath = Join-Path $env:TEMP "ffmpeg-essentials.zip"
        $ffmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        try {
            Invoke-WebRequest -Uri $ffmpegUrl -OutFile $zipPath -UseBasicParsing
            Expand-Archive -LiteralPath $zipPath -DestinationPath $toolsDir -Force
            $binFolder = Get-ChildItem -Path $toolsDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
            if ($binFolder) {
                $binPath = $binFolder.DirectoryName
                $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
                if ($userPath -notmatch [regex]::Escape($binPath)) {
                    [System.Environment]::SetEnvironmentVariable("Path", "$userPath;$binPath", "User")
                }
                Refresh-Path
                $ffmpegInstalled = $true
            }
        } catch {
            Write-Warn "Не удалось автоматически скачать FFmpeg: $($_.Exception.Message)"
        }
    }

    if (Have-Cmd ffmpeg) {
        Write-OK "FFmpeg успешно установлен!"
    } else {
        Write-Warn "FFmpeg не удалось зарегистрировать в PATH сразу. Студия запустится, но шаги монтажа могут потребовать перезагрузки консоли."
    }
}

# -------------------------------------------------------------
# ШАГ 4: Создание .venv и быстрая установка зависимостей через uv
# -------------------------------------------------------------
Write-Step "Шаг 4/6: Настройка окружения Python (.venv) и библиотек"

$venvDir = Join-Path $Root ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "    Создание виртуального окружения .venv..." -ForegroundColor DarkGray
    $pyParts = $pyCmd -split ' '
    $baseExe = $pyParts[0]
    $baseArgs = if ($pyParts.Length -gt 1) { $pyParts[1..($pyParts.Length - 1)] } else { @() }
    & $baseExe @baseArgs -m venv $venvDir
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Err "Не удалось создать .venv ($venvPython)"
    exit 1
}
Write-OK "Окружение .venv готово."

Write-Host "    Проверка менеджера быстрых пакетов uv..." -ForegroundColor DarkGray
$venvUv = Join-Path $venvDir "Scripts\uv.exe"
if (-not (Test-Path -LiteralPath $venvUv)) {
    & $venvPython -m pip install --quiet --upgrade pip uv 2>&1 | Out-Null
}

$repoRoot = (Resolve-Path -LiteralPath $Root).Path
Write-Host "    Установка зависимостей Video Pipeline (через uv, ~30-45 секунд)..." -ForegroundColor Cyan

if (Test-Path -LiteralPath $venvUv) {
    & $venvUv pip install -e "${repoRoot}[whisper]"
} else {
    & $venvPython -m pip install -e "${repoRoot}[whisper]"
}

# Проверка NVIDIA и ASR
$hasNvidiaGpu = $false
try {
    $smi = & nvidia-smi 2>$null
    if ($LASTEXITCODE -eq 0) { $hasNvidiaGpu = $true }
} catch { }

$asrBackend = "whisper"
if (Test-Path -LiteralPath $targetEnv) {
    $match = Select-String -Path $targetEnv -Pattern '^\s*ASR_BACKEND\s*=\s*(\S+)' | Select-Object -First 1
    if ($match) {
        $asrBackend = $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'").ToLower()
    }
}

if ($asrBackend -eq "nvidia") {
    if ($hasNvidiaGpu) {
        $nemoAlready = $false
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $nemoCheck = & $venvPython -c "try: import nemo.collections.asr; print('NEMO_OK')`nexcept Exception: print('NEMO_NO')" 2>$null
        $ErrorActionPreference = $prevEAP
        if ("$nemoCheck" -match "NEMO_OK") {
            $nemoAlready = $true
            Write-OK "NVIDIA NeMo ASR уже установлен."
        } else {
            Write-Host "    Обнаружена видеокарта NVIDIA и ASR_BACKEND=nvidia. Доустанавливаю пакет NeMo ASR..." -ForegroundColor Cyan
            $nemoOk = $false
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            if (Test-Path -LiteralPath $venvUv) {
                & $venvUv pip install -e "${repoRoot}[nvidia]" 2>$null
                if ($LASTEXITCODE -eq 0) { $nemoOk = $true }
            }
            if (-not $nemoOk) {
                & $venvPython -m pip install -e "${repoRoot}[nvidia]" 2>$null
                if ($LASTEXITCODE -eq 0) { $nemoOk = $true }
            }
            $ErrorActionPreference = $prevEAP
            if ($nemoOk) {
                Write-OK "NVIDIA NeMo ASR успешно установлен."
            } else {
                Write-Warn "Установка NeMo не завершилась (сбой загрузки с PyPI). Студия продолжит работу на Whisper ASR."
            }
        }
    } else {
        Write-Warn "В .env указан ASR_BACKEND=nvidia, но видеокарта NVIDIA не найдена. Будет использоваться Whisper."
    }
} else {
    Write-OK "Аудио-распознавание (ASR): Whisper (легковесный, готов к работе)."
}

# Валидация импортов
$checkImports = & $venvPython -c "import fastapi, sqlalchemy, playwright, faster_whisper; print('IMPORTS_OK')" 2>&1
if ($checkImports -match "IMPORTS_OK") {
    Write-OK "Все основные библиотеки успешно проверены."
} else {
    Write-Warn "Проверка импортов завершилась с предупреждением: $checkImports"
}

# -------------------------------------------------------------
# ШАГ 5: Проверка веб-интерфейса (web/out)
# -------------------------------------------------------------
Write-Step "Шаг 5/6: Проверка веб-интерфейса Студии"

$webOutHtml = Join-Path $Root "web\out\index.html"
if (Test-Path -LiteralPath $webOutHtml) {
    Write-OK "Скомпилированный интерфейс найден (web/out/index.html). Node.js не требуется!"
} else {
    Write-Warn "Папка web/out отсутствует. Интерфейс будет работать в режиме fallback или потребует сборки."
}

# Папка data/
$dataDir = Join-Path $Root "data"
if (-not (Test-Path -LiteralPath $dataDir)) {
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
}
Write-OK "Папка данных (data/) готова."

# -------------------------------------------------------------
# ШАГ 6: Создание ярлыка на Рабочем столе с фирменной иконкой
# -------------------------------------------------------------
Write-Step "Шаг 6/6: Создание ярлыка на Рабочем столе"

try {
    $desktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
    $shortcutPath = Join-Path $desktopPath "Video Pipeline Studio.lnk"
    $targetCmd = Join-Path $Root "STUDIO.cmd"
    $iconPath = Join-Path $Root "assets\icon.ico"

    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetCmd
    $shortcut.Arguments = "1"
    $shortcut.WorkingDirectory = $Root
    if (Test-Path -LiteralPath $iconPath) {
        $shortcut.IconLocation = "$iconPath,0"
    }
    $shortcut.Description = "Запуск Video Pipeline Studio"
    $shortcut.Save()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh) | Out-Null

    Write-OK "Ярлык успешно создан на Рабочем столе с иконкой Студии!"
} catch {
    Write-Warn "Не удалось автоматически создать ярлык: $($_.Exception.Message)"
}

# -------------------------------------------------------------
# ФИНИШ
# -------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "           УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА!                     " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Для повседневного запуска используйте ярлык на Рабочем столе:" -ForegroundColor White
Write-Host "    [Video Pipeline Studio] -> запускает Студию в 1 клик" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Адрес в браузере: http://127.0.0.1:8765" -ForegroundColor Cyan
Write-Host ""

if (-not $NonInteractive) {
    $startNow = Read-Host "Запустить Студию прямо сейчас? (Y/n, Enter = Да)"
    if ($startNow -eq "" -or $startNow -match "^[yYдД]") {
        Write-Host "Запуск Video Pipeline Studio..." -ForegroundColor Green
        Start-Process -FilePath $targetCmd -ArgumentList "1" -WorkingDirectory $Root
    }
}

exit 0
