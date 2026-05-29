# Видимое окно Chrome для входа в outsee.io (бот подключается по CDP :29229).
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\scripts\VpBrowserProfile.ps1"
Set-Location (Get-VpRepoRoot)

$outseeUrl = "https://outsee.io/video?model=veo-3-1-fast"
$userDataDir = Get-VpBrowserUserDataDir
$cdpPort = 29229

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ОКНО CHROME ДЛЯ OUTSEE (вход вручную)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Профиль Chrome: $userDataDir" -ForegroundColor Yellow
Write-Host "CDP порт: $cdpPort" -ForegroundColor DarkGray
Write-Host ""

Start-VpChromeCdp -Port $cdpPort -OpenUrl $outseeUrl

Write-Host ""
Write-Host "После входа в outsee нажми Enter — сделаю скан кнопки Generate..." -ForegroundColor Cyan
Read-Host | Out-Null

$venvPy = Join-Path (Get-VpRepoRoot) ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    & $venvPy -m app.bots.outsee recon-generate video
    Write-Host ""
    Write-Host "Файлы: data\outsee_dumps\recon_generate_video_*" -ForegroundColor Yellow
} else {
    Write-Host "venv не найден — сначала install.ps1" -ForegroundColor Red
}

Write-Host ""
Write-Host "Окно Chrome НЕ закрывай, пока идёт генерация." -ForegroundColor Yellow
pause
