# video-pipeline: первичная установка на новом Windows-ПК (любой пользователь).
#
# Из корня репо:
#   powershell -ExecutionPolicy Bypass -File .\scripts\Install-NewPc.ps1
#
# Из любой папки (клонирует в %USERPROFILE%\video-pipeline):
#   powershell -ExecutionPolicy Bypass -File C:\path\to\scripts\Install-NewPc.ps1
#
# Одной строкой из интернета (после clone bootstrap.ps1):
#   cd $env:USERPROFILE\video-pipeline
#   powershell -ExecutionPolicy Bypass -File .\scripts\Install-NewPc.ps1 -SkipClone

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE "video-pipeline"),
    [string]$Branch = "fix/text-save-persistence-v153",
    [string]$RepoUrl = "https://github.com/rohos228-spec/video-pipeline.git",
    [switch]$SkipClone,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "    [ok] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "    [!] $msg" -ForegroundColor Yellow
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

function Ensure-Winget {
    Write-Step "Проверяю winget"
    if (-not (Have-Cmd winget)) {
        Write-Host "ERROR: winget не найден. Поставь App Installer из Microsoft Store и перезапусти PowerShell." -ForegroundColor Red
        Write-Host "       https://apps.microsoft.com/detail/9NBLGGH4NNS1" -ForegroundColor Red
        exit 1
    }
    Write-OK "winget есть"
}

function Ensure-Git {
    Write-Step "Проверяю Git"
    if (Have-Cmd git) {
        Write-OK "Git есть"
        return
    }
    Write-Warn "Git не найден — ставлю через winget"
    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    if (-not (Have-Cmd git)) {
        Write-Host "ERROR: Git не появился в PATH. Закрой PowerShell, открой новый и запусти скрипт снова." -ForegroundColor Red
        exit 1
    }
    Write-OK "Git установлен"
}

function Ensure-Node {
    Write-Step "Проверяю Node.js"
    Refresh-Path
    if (Have-Cmd node) {
        Write-OK "Node $(node --version)"
        return
    }
    Write-Warn "Node.js не найден — ставлю LTS через winget"
    winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    if (Have-Cmd node) {
        Write-OK "Node $(node --version)"
    } else {
        Write-Warn "Node.js всё ещё не в PATH — закрой PowerShell и открой новый"
    }
}

function Ensure-Chrome {
    Write-Step "Проверяю Chrome"
    $chromePaths = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )
    $chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($null -eq $chrome) {
        Write-Warn "Chrome не найден. Скачай: https://www.google.com/chrome/"
        Write-Warn "Без Chrome шаги ChatGPT/outsee работать не будут."
    } else {
        Write-OK "Chrome: $chrome"
    }
}

function Sync-Repo {
    param([string]$Dir, [string]$Url, [string]$Br)

    $parent = Split-Path -Parent $Dir
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    if (Test-Path (Join-Path $Dir ".git")) {
        Write-Step "Обновляю репозиторий в $Dir"
        git -C $Dir fetch origin $Br
        git -C $Dir checkout -B $Br "origin/$Br"
        git -C $Dir reset --hard "origin/$Br"
        Write-OK "git $(git -C $Dir rev-parse --short HEAD) @ $Br"
        return
    }

    if (Test-Path $Dir) {
        Write-Host "ERROR: папка $Dir уже существует, но это не git-репозиторий." -ForegroundColor Red
        Write-Host "       Укажи другой путь: -InstallDir C:\Projects\video-pipeline" -ForegroundColor Yellow
        exit 1
    }

    Write-Step "Клонирую $Url -> $Dir (ветка $Br)"
    git clone --branch $Br $Url $Dir
    Write-OK "git $(git -C $Dir rev-parse --short HEAD)"
}

Write-Host ""
Write-Host "video-pipeline — установка на новый ПК" -ForegroundColor Cyan
Write-Host "Пользователь: $env:USERNAME" -ForegroundColor Gray
Write-Host "Папка:        $InstallDir" -ForegroundColor Gray
Write-Host "Ветка:        $Branch" -ForegroundColor Gray
Write-Host ""

Ensure-Winget
Ensure-Git
Ensure-Node
Ensure-Chrome

if (-not $SkipClone) {
    Sync-Repo -Dir $InstallDir -Url $RepoUrl -Br $Branch
}

if (-not (Test-Path (Join-Path $InstallDir "pyproject.toml"))) {
    Write-Host "ERROR: pyproject.toml не найден в $InstallDir" -ForegroundColor Red
    exit 1
}

Set-Location -LiteralPath $InstallDir
Write-Step "Запускаю install.ps1 в $InstallDir"
$installArgs = @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $InstallDir "install.ps1"))
if ($NonInteractive) { $installArgs += "-NonInteractive" }
& powershell @installArgs

Write-Host ""
Write-Host "===================================" -ForegroundColor Green
Write-Host "  Установка завершена!" -ForegroundColor Green
Write-Host "===================================" -ForegroundColor Green
Write-Host ""
Write-Host "Папка: $InstallDir" -ForegroundColor White
    Write-Host "1. Двойной клик STUDIO.cmd -> [2] Обновить и запустить" -ForegroundColor White
Write-Host "2. Браузер: http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host ""
Write-Host "Chrome для ChatGPT/outsee (один раз, залогиниться):" -ForegroundColor DarkGray
Write-Host "  Start-Chrome.cmd" -ForegroundColor DarkGray
Write-Host ""
