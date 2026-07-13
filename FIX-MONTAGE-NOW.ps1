# Одна кнопка: фикс WinError 1314 + убрать двойной Whisper + перемонтаж #20
#
# Запуск (скопируйте ОДНУ строку, без логов):
#   powershell -ExecutionPolicy Bypass -File .\FIX-MONTAGE-NOW.ps1
#
# Только скачать фикс и перезапустить бэкенд (без remount):
#   powershell -ExecutionPolicy Bypass -File .\FIX-MONTAGE-NOW.ps1 -SkipRemount

param(
    [string]$Topic = "дожд",
    [int]$ProjectId = 0,
    [switch]$SkipRemount,
    [switch]$SkipDownload
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

$Branch = "cursor/remount-video-fc98"
$Repo = "rohos228-spec/video-pipeline"
$BaseUrl = "https://raw.githubusercontent.com/$Repo/$Branch"

$HotfixFiles = @(
    "app/services/whisper.py",
    "app/settings.py",
    "app/orchestrator/steps/assemble.py",
    "app/orchestrator/steps/generate_audio.py",
    "app/services/remount_video.py",
    "remount_video.py",
    "run-backend.ps1",
    "scripts/download_whisper.py"
)

function Ensure-EnvLine([string]$Path, [string]$Key, [string]$Value) {
    $line = "$Key=$Value"
    if (-not (Test-Path $Path)) {
        Add-Content -Path $Path -Value $line -Encoding UTF8
        return
    }
    $raw = Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction SilentlyContinue
    $found = $false
    $out = foreach ($r in $raw) {
        if ($r -match "^\s*$([regex]::Escape($Key))\s*=") {
            $found = $true
            $line
        } else {
            $r
        }
    }
    if (-not $found) { $out += $line }
    Set-Content -LiteralPath $Path -Value $out -Encoding UTF8
}

Write-Host ""
Write-Host "=== FIX MONTAGE (Whisper WinError 1314) ===" -ForegroundColor Cyan
Write-Host "Repo: $Root" -ForegroundColor DarkGray
Write-Host ""

# 1) Переменные окружения (сразу для этого процесса)
$env:HF_HUB_DISABLE_SYMLINKS = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "0"
$env:SUBTITLE_REWHISPER_ON_ASSEMBLE = "false"

$envPath = Join-Path $Root ".env"
Ensure-EnvLine -Path $envPath -Key "HF_HUB_DISABLE_SYMLINKS" -Value "1"
Ensure-EnvLine -Path $envPath -Key "SUBTITLE_REWHISPER_ON_ASSEMBLE" -Value "false"
Write-Host "[OK] .env: HF_HUB_DISABLE_SYMLINKS=1, SUBTITLE_REWHISPER_ON_ASSEMBLE=false" -ForegroundColor Green

# 2) Скачать фикс с GitHub (если git pull не делали)
if (-not $SkipDownload) {
    Write-Host ""
    Write-Host "Скачиваю фикс с ветки $Branch ..." -ForegroundColor Cyan
    $ok = 0
    foreach ($rel in $HotfixFiles) {
        $dest = Join-Path $Root ($rel -replace "/", "\")
        $parent = Split-Path -Parent $dest
        if ($parent -and -not (Test-Path $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        $url = "$BaseUrl/$rel"
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
            Write-Host "  + $rel" -ForegroundColor Gray
            $ok++
        } catch {
            Write-Host "  ! $rel — $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    Write-Host "Скачано файлов: $ok" -ForegroundColor $(if ($ok -ge 5) { "Green" } else { "Yellow" })
}

# 3) Очистить битый кэш HF (symlink WinError 1314)
$hfBroken = Join-Path $env:USERPROFILE ".cache\huggingface\hub\models--Systran--faster-whisper-large-v3"
if (Test-Path $hfBroken) {
    Write-Host ""
    Write-Host "Очищаю битый кэш Whisper (symlink error) ..." -ForegroundColor Yellow
    Remove-Item -LiteralPath $hfBroken -Recurse -Force -ErrorAction SilentlyContinue
}

Get-ChildItem -Path (Join-Path $Root "app") -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 4) Остановить зависший бэкенд
$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) {
    Write-Host ""
    Write-Host "Останавливаю бэкенд на :8765 ..." -ForegroundColor Cyan
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet
    Start-Sleep -Seconds 2
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: .venv не найден. Запустите .\install.ps1" -ForegroundColor Red
    exit 1
}

# 5) Предзагрузка модели Whisper (чтобы сборка не падала на HF)
Write-Host ""
Write-Host "Предзагрузка Whisper (может занять 1–3 мин) ..." -ForegroundColor Cyan
& $py scripts\download_whisper.py medium 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

# 6) Запуск бэкенда в новом окне
Write-Host ""
Write-Host "Запускаю бэкенд в новом окне ..." -ForegroundColor Cyan
$backendPs1 = Join-Path $Root "run-backend.ps1"
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", $backendPs1
) -WorkingDirectory $Root

Write-Host "Жду http://127.0.0.1:8765/api/health (до 90 сек) ..." -ForegroundColor Gray
$ready = $false
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if ($ready) {
    Write-Host "[OK] Бэкенд отвечает" -ForegroundColor Green
} else {
    Write-Host "[!] Бэкенд ещё стартует — remount можно запустить вручную позже" -ForegroundColor Yellow
}

if ($SkipRemount) {
    Write-Host ""
    Write-Host "Готово (без remount). Откройте Studio: http://127.0.0.1:8765" -ForegroundColor Green
    exit 0
}

# 7) Перемонтаж проекта
Write-Host ""
Write-Host "Запускаю перемонтаж ..." -ForegroundColor Cyan
$remountArgs = @("-m", "remount_video")
if ($ProjectId -gt 0) {
    $remountArgs += "$ProjectId"
} else {
    $remountArgs += "--topic"
    $remountArgs += $Topic
}
& $py @remountArgs
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "ГОТОВО. Следите за логом в окне run-backend:" -ForegroundColor Green
    Write-Host "  - НЕ должно быть: re-whisper voice_full" -ForegroundColor DarkGray
    Write-Host "  - Должно быть: assemble: words.json актуален ИЛИ сразу ffmpeg" -ForegroundColor DarkGray
} else {
    Write-Host "Remount завершился с кодом $code — смотрите лог выше и data\backend-*.log" -ForegroundColor Yellow
}
exit $code
