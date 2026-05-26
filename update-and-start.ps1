# Обновить репозиторий и запустить Video Pipeline Studio
# Двойной клик: update-and-start.cmd
# Или: powershell -ExecutionPolicy Bypass -File .\update-and-start.ps1
#
# По умолчанию ветка devin/windows-installer. Другая ветка:
#   .\update-and-start.ps1 -Branch cursor/fix-chatgpt-batch-attach-977b

[CmdletBinding()]
param(
    [string]$Branch = "devin/windows-installer",
    [switch]$BackendOnly,
    [switch]$SkipNpm,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }

if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: запустите из корня video-pipeline" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Video Pipeline — update + start" -ForegroundColor White
Write-Host "Папка: $Root" -ForegroundColor DarkGray
Write-Host ""

Write-Step "git fetch + pull ($Branch)"
git fetch origin $Branch 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
git checkout $Branch 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
git pull origin $Branch 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: git pull не удался (закройте Studio/Cursor и повторите)" -ForegroundColor Red
    exit 1
}
Write-Ok "git $(git rev-parse --short HEAD)"

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Warn "нет .venv — запускаю install.ps1"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "install.ps1") -NonInteractive
    if (-not (Test-Path $py)) {
        Write-Host "ERROR: install.ps1 не создал .venv" -ForegroundColor Red
        exit 1
    }
}

Write-Step "pip install -e .[dev]"
& $py -m pip install -e ".[dev]" 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
Write-Ok "python deps"

if (-not $SkipNpm) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        $guess = Join-Path ${env:ProgramFiles} "nodejs\npm.cmd"
        if (Test-Path $guess) { $npm = $guess }
    }
    if ($npm) {
        Write-Step "npm install + build (web/out)"
        Push-Location (Join-Path $Root "web")
        & $npm install 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        & $npm run build 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        Pop-Location
        if (Test-Path (Join-Path $Root "web\out\index.html")) {
            Write-Ok "web UI built"
        } else {
            Write-Warn "web/out/index.html нет — в Launcher: 6 Build Web UI"
        }
    } else {
        Write-Warn "npm не найден — пропускаю сборку UI"
    }
}

Write-Host ""
Write-Host "Готово. Запуск…" -ForegroundColor Green
Write-Host "  Studio:  http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host "  Не :3000. Окно бэкенда не закрывать." -ForegroundColor DarkGray
Write-Host ""

if ($NoLaunch) {
    Write-Host "Флаг -NoLaunch: запуск вручную:" -ForegroundColor Gray
    Write-Host "  .\VideoPipelineStudio.cmd" -ForegroundColor White
    Write-Host "  или .\run-backend.ps1" -ForegroundColor White
    exit 0
}

if ($BackendOnly) {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:8765"
    exit 0
}

$launcher = Join-Path $Root "VideoPipelineStudio.cmd"
if (Test-Path $launcher) {
    Start-Process $launcher -WorkingDirectory $Root
    Write-Ok "VideoPipelineStudio.cmd"
    Write-Host "В Launcher: 4 Stop → 2 Start Studio (или * Quick start)" -ForegroundColor DarkGray
} else {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $Root "run-backend.ps1")
    ) -WorkingDirectory $Root
    Write-Ok "run-backend.ps1"
}
