# Видимое окно Chrome для входа в outsee.io (бот подключается по CDP :29229).
$ErrorActionPreference = "Stop"
Set-Location "C:\Users\Love Space\video-pipeline"

$outseeUrl = "https://outsee.io/video?model=veo-3-1-fast"
$userDataDir = "$env:USERPROFILE\.vp_browser_data"
$cdpPort = 29229

$chromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Host "ERROR: Chrome не найден. Установи Google Chrome." -ForegroundColor Red
    pause
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ОКНО CHROME ДЛЯ OUTSEE (вход вручную)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Сейчас откроется ОТДЕЛЬНОЕ окно Chrome." -ForegroundColor Yellow
Write-Host "  1) Войди в аккаунт outsee.io" -ForegroundColor White
Write-Host "  2) Оставь это окно ОТКРЫТЫМ" -ForegroundColor White
Write-Host "  3) Потом запусти RECON-OUTSEE.cmd (скан кнопок)" -ForegroundColor White
Write-Host "     или RUN.cmd (backend + генерация)" -ForegroundColor White
Write-Host ""
Write-Host "Профиль Chrome: $userDataDir" -ForegroundColor DarkGray
Write-Host "CDP порт: $cdpPort" -ForegroundColor DarkGray
Write-Host ""

# Если CDP уже жив — только открываем вкладку outsee в том же профиле
$cdpOk = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$cdpPort/json/version" -TimeoutSec 2 -UseBasicParsing
    if ($r.StatusCode -eq 200) { $cdpOk = $true }
} catch { }

if ($cdpOk) {
    Write-Host "Chrome с CDP уже запущен — открываю outsee в новой вкладке..." -ForegroundColor Green
    Start-Process -FilePath $chrome -ArgumentList @(
        "--user-data-dir=$userDataDir",
        $outseeUrl
    )
} else {
    Write-Host "Запускаю Chrome с remote-debugging (видимое окно)..." -ForegroundColor Green
    Start-Process -FilePath $chrome -ArgumentList @(
        "--remote-debugging-port=$cdpPort",
        "--user-data-dir=$userDataDir",
        "--no-first-run",
        "--no-default-browser-check",
        $outseeUrl
    )
    $waited = 0
    while ($waited -lt 20) {
        Start-Sleep -Seconds 1
        $waited++
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$cdpPort/json/version" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) {
                Write-Host "CDP готов (через $waited сек)." -ForegroundColor Green
                break
            }
        } catch { }
    }
}

Write-Host ""
Write-Host "После входа в outsee нажми Enter — сделаю скан кнопки Generate..." -ForegroundColor Cyan
Read-Host | Out-Null

if (Test-Path ".\.venv\Scripts\python.exe") {
    & ".\.venv\Scripts\python.exe" -m app.bots.outsee recon-generate video
    Write-Host ""
    Write-Host "Файлы: data\outsee_dumps\recon_generate_video_*" -ForegroundColor Yellow
    Write-Host "Пришли .json и .png в чат агенту." -ForegroundColor Yellow
} else {
    Write-Host "venv не найден — сначала UPDATE.cmd / install" -ForegroundColor Red
}

Write-Host ""
Write-Host "Окно Chrome НЕ закрывай, пока идёт генерация." -ForegroundColor Yellow
pause
