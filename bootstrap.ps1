# video-pipeline: bootstrap для нового ПК
# Запуск из любой папки (одна строка в PowerShell):
#
#   iwr https://raw.githubusercontent.com/rohos228-spec/video-pipeline/refs/heads/devin/windows-installer/bootstrap.ps1 -UseBasicParsing | iex
#
# Что делает:
#   1. Ставит git через winget, если его нет.
#   2. Клонирует репо в текущую папку.
#   3. Переключается на ветку с инсталлятором.
#   4. Запускает install.ps1.

$ErrorActionPreference = "Stop"

$REPO_URL    = "https://github.com/rohos228-spec/video-pipeline.git"
$BRANCH      = "devin/windows-installer"
$DIR_NAME    = "video-pipeline"

function Have-Cmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "==> video-pipeline bootstrap" -ForegroundColor Cyan

# 1. Проверка winget
if (-not (Have-Cmd winget)) {
    Write-Host "ERROR: winget не найден. Поставь 'App Installer' из Microsoft Store, перезапусти PowerShell, запусти эту команду снова." -ForegroundColor Red
    Write-Host "       https://apps.microsoft.com/detail/9NBLGGH4NNS1" -ForegroundColor Red
    exit 1
}

# 2. git
if (-not (Have-Cmd git)) {
    Write-Host "==> Ставлю Git..." -ForegroundColor Cyan
    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    if (-not (Have-Cmd git)) {
        Write-Host "ERROR: git не появился в PATH. Закрой PowerShell, открой новый и запусти команду снова." -ForegroundColor Red
        exit 1
    }
}

# 3. Клон или pull
if (Test-Path $DIR_NAME) {
    Write-Host "==> Папка $DIR_NAME уже есть, обновляю..." -ForegroundColor Cyan
    Push-Location $DIR_NAME
    git fetch origin
    git checkout $BRANCH
    git pull origin $BRANCH
    Pop-Location
} else {
    Write-Host "==> Клонирую $REPO_URL..." -ForegroundColor Cyan
    git clone --branch $BRANCH $REPO_URL $DIR_NAME
}

# 4. Запуск install.ps1
Set-Location $DIR_NAME
Write-Host ""
Write-Host "==> Запускаю install.ps1" -ForegroundColor Cyan
Write-Host ""
& powershell -ExecutionPolicy Bypass -File ".\install.ps1"
