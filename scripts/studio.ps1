# Единый лаунчер Video Pipeline Studio (меню на русском)
# Вызывается из STUDIO.cmd в корне репозитория.

param(
    [ValidateSet("1", "2", "3", "4", "")]
    [string]$Action = ""
)

$ErrorActionPreference = "Continue"
$StudioBranch = "main"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

try { chcp 65001 | Out-Null } catch { }
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-StudioMsg {
    param(
        [string]$Text,
        [string]$Color = "Gray"
    )
    Write-Host $Text -ForegroundColor $Color
}

function Test-RepoRoot {
    if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
        Write-StudioMsg "ОШИБКА: запустите STUDIO.cmd из корня репозитория video-pipeline." "Red"
        return $false
    }
    return $true
}

function Invoke-StudioPause {
    Write-Host ""
    Write-Host "Нажмите Enter для продолжения..." -ForegroundColor DarkGray
    Read-Host | Out-Null
}

function Stop-StudioBackend {
    $stop = Join-Path $Root "scripts\stop-backend.ps1"
    if (Test-Path $stop) {
        & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet 2>$null
    }
    Start-Sleep -Seconds 2
}

function Open-StudioBrowser {
    try { Start-Process "http://127.0.0.1:8765" } catch { }
}

function Test-StudioHealth {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:8765/api/health" -TimeoutSec 3 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Start-StudioChromeCdp {
    $chrome = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
    try {
        $r = Invoke-WebRequest "http://localhost:29229/json/version" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { return }
    } catch { }
    if (Test-Path $chrome) {
        Write-StudioMsg "==> Запуск Chrome CDP :29229 (для ChatGPT/outsee)" "Yellow"
        Start-Process -FilePath $chrome -ArgumentList @(
            "--remote-debugging-port=29229",
            "--user-data-dir=$env:USERPROFILE\.vp_browser_data"
        )
        Start-Sleep -Seconds 2
    } else {
        Write-StudioMsg "Chrome не найден — шаги с браузером могут не работать." "Yellow"
    }
}

function Start-StudioBackendWindow {
    $rb = Join-Path $Root "scripts\run-backend.ps1"
    if (-not (Test-Path $rb)) {
        Write-StudioMsg "ОШИБКА: не найден scripts\run-backend.ps1" "Red"
        return $false
    }
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-StudioMsg "ОШИБКА: .venv не найден. Запустите install.ps1 или пункт [3]." "Red"
        return $false
    }
    & $py -c "from app.web.api import create_app; create_app()" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ОШИБКА: Python create_app() не прошёл. Попробуйте [2] или [3]." "Red"
        return $false
    }
    Write-StudioMsg "==> Запуск бэкенда (отдельное окно)..." "Cyan"
    Start-Process powershell.exe -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $rb
    ) -WorkingDirectory $Root
    $deadline = (Get-Date).AddSeconds(120)
    $started = Get-Date
    while ((Get-Date) -lt $deadline) {
        if (Test-StudioHealth) {
            Write-StudioMsg "OK: бэкенд отвечает на :8765" "Green"
            try {
                $sv = Invoke-RestMethod "http://127.0.0.1:8765/api/studio-version" -TimeoutSec 5
                Write-StudioMsg "Версия UI: $($sv.label)" "Green"
            } catch { }
            return $true
        }
        Start-Sleep -Milliseconds 500
        $waitSec = [int]((Get-Date) - $started).TotalSeconds
        if ($waitSec -ge 10 -and ($waitSec % 10) -eq 0) {
            Write-StudioMsg "Ждём :8765 ... ${waitSec}с (см. окно бэкенда)" "DarkGray"
        }
    }
    Write-StudioMsg "Таймаут ожидания :8765 — откройте http://127.0.0.1:8765 вручную" "Yellow"
    return $false
}

function Invoke-StudioStart {
    Write-StudioMsg "=== [1] Запуск студии ===" "Cyan"
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-StudioMsg "ВНИМАНИЕ: web/out отсутствует. Сначала [3] Починить установку." "Yellow"
    }
    Stop-StudioBackend
    Start-StudioChromeCdp
    $job = Start-Job -ScriptBlock {
        $deadline = (Get-Date).AddSeconds(120)
        while ((Get-Date) -lt $deadline) {
            try {
                $r = Invoke-WebRequest "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 2
                if ($r.StatusCode -eq 200) {
                    Start-Process "http://127.0.0.1:8765"
                    return
                }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not (Start-StudioBackendWindow)) {
        Stop-Job $job -ErrorAction SilentlyContinue
        Remove-Job $job -Force -ErrorAction SilentlyContinue
        return $false
    }
    Start-Sleep -Seconds 1
    Open-StudioBrowser
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    Write-StudioMsg "Студия: http://127.0.0.1:8765 (Ctrl+F5 в браузере)" "Green"
    return $true
}

function Invoke-StudioGitStash {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-StudioMsg "ОШИБКА: git не найден в PATH." "Red"
        return $false
    }
    $status = git -C $Root status --porcelain 2>&1
    if (-not $status) {
        Write-StudioMsg "Локальных изменений нет — stash не нужен." "Gray"
        return $true
    }
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $msg = "studio: автосохранение перед обновлением $stamp"
    Write-StudioMsg "==> Сохраняю локальные изменения в git stash..." "Cyan"
    Write-StudioMsg "    (data/, prompts/, logs/, .env в .gitignore — не затрагиваются reset)" "DarkGray"
    git -C $Root stash push -u -m $msg 2>&1 | ForEach-Object { Write-StudioMsg $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: git stash не удался. Продолжаю обновление." "Yellow"
    } else {
        Write-StudioMsg "OK: stash создан. Восстановить: git stash list / git stash pop" "Green"
    }
    return $true
}

function Invoke-StudioGitUpdate {
    Write-StudioMsg "==> git fetch origin $StudioBranch" "Cyan"
    git -C $Root fetch origin $StudioBranch 2>&1 | ForEach-Object { Write-StudioMsg $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ОШИБКА: git fetch не удался. Проверьте интернет и доступ к GitHub." "Red"
        return $false
    }
    Write-StudioMsg "==> git reset --hard origin/$StudioBranch" "Cyan"
    git -C $Root reset --hard "origin/$StudioBranch" 2>&1 | ForEach-Object { Write-StudioMsg $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ОШИБКА: git reset не удался." "Red"
        return $false
    }
    git -C $Root checkout -B $StudioBranch "origin/$StudioBranch" 2>&1 | ForEach-Object { Write-StudioMsg $_ }
    $head = (git -C $Root rev-parse --short HEAD 2>$null).Trim()
    Write-StudioMsg "OK: код обновлён до $head (origin/$StudioBranch)" "Green"
    return $true
}

function Invoke-StudioPipInstall {
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-StudioMsg "ОШИБКА: .venv не найден. Запустите install.ps1." "Red"
        return $false
    }
    $spec = (Resolve-Path -LiteralPath $Root).Path
    $env:PIP_DEFAULT_TIMEOUT = "300"
    Write-StudioMsg "==> pip install -e ." "Cyan"
    Push-Location -LiteralPath $Root
    & $py -m pip install --default-timeout=300 -e $spec
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) {
        Write-StudioMsg "ОШИБКА: pip install не удался." "Red"
        return $false
    }
    Write-StudioMsg "OK: Python-зависимости установлены." "Green"
    return $true
}

function Invoke-StudioUpdateAndStart {
    Write-StudioMsg "=== [2] Обновить и запустить (origin/$StudioBranch) ===" "Cyan"
    Invoke-StudioGitStash | Out-Null
    if (-not (Invoke-StudioGitUpdate)) {
        return $false
    }
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $py) {
        & $py -c "import fastapi, sqlalchemy, playwright" 2>$null
        if ($LASTEXITCODE -ne 0) {
            if (-not (Invoke-StudioPipInstall)) { return $false }
        } else {
            Write-StudioMsg "OK: основные Python-пакеты на месте." "Green"
        }
    } else {
        Write-StudioMsg "ОШИБКА: .venv не найден. Сначала install.ps1." "Red"
        return $false
    }
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-StudioMsg "ВНИМАНИЕ: web/out отсутствует после обновления — запускаю [3] сборку UI..." "Yellow"
        if (-not (Invoke-StudioRepairWeb)) { return $false }
    }
    Stop-StudioBackend
    return (Invoke-StudioStart)
}

function Invoke-StudioRepairWeb {
    $webDir = Join-Path $Root "web"
    if (-not (Test-Path (Join-Path $webDir "package.json"))) {
        Write-StudioMsg "ОШИБКА: web/package.json не найден." "Red"
        return $false
    }
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        $npmGuess = Join-Path ${env:ProgramFiles} "nodejs\npm.cmd"
        if (Test-Path $npmGuess) { $npm = Get-Command $npmGuess }
    }
    if (-not $npm) {
        Write-StudioMsg "ОШИБКА: npm не найден. Установите Node.js (install.ps1 / winget)." "Red"
        return $false
    }
    Write-StudioMsg "==> npm install (web/)" "Cyan"
    Push-Location -LiteralPath $webDir
    & npm install
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-StudioMsg "ОШИБКА: npm install не удался." "Red"
        return $false
    }
    Write-StudioMsg "==> npm run build (web/)" "Cyan"
    & npm run build
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) {
        Write-StudioMsg "ОШИБКА: сборка web не удалась." "Red"
        return $false
    }
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-StudioMsg "ОШИБКА: после сборки нет web/out/index.html" "Red"
        return $false
    }
    Write-StudioMsg "OK: web/out собран." "Green"
    return $true
}

function Invoke-StudioRepair {
    Write-StudioMsg "=== [3] Починить установку ===" "Cyan"
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-StudioMsg "ОШИБКА: .venv не найден. Запустите install.ps1 для первичной установки." "Red"
        return $false
    }
    if (-not (Invoke-StudioPipInstall)) { return $false }
    Write-StudioMsg "==> playwright install chromium" "Cyan"
    & $py -m playwright install chromium 2>&1 | ForEach-Object { Write-StudioMsg $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: playwright install завершился с ошибкой." "Yellow"
    } else {
        Write-StudioMsg "OK: Playwright Chromium установлен." "Green"
    }
    if (-not (Invoke-StudioRepairWeb)) { return $false }
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        $ff = (ffmpeg -version 2>&1 | Select-Object -First 1)
        Write-StudioMsg "OK: FFmpeg — $ff" "Green"
    } else {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: ffmpeg не в PATH. Установите через install.ps1 / winget." "Yellow"
    }
    & $py -c "import fastapi, sqlalchemy, playwright; print('imports OK')" 2>&1 | ForEach-Object { Write-StudioMsg $_ "Green" }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ОШИБКА: проверка импортов не прошла." "Red"
        return $false
    }
    Write-StudioMsg "Починка завершена." "Green"
    return $true
}

function Write-DoctorLine {
    param([string]$Line, [System.Collections.Generic.List[string]]$Buffer)
    Write-Host $Line
    [void]$Buffer.Add($Line)
}

function Invoke-StudioDoctor {
    Write-StudioMsg "=== [4] Диагностика ===" "Cyan"
    $lines = New-Object System.Collections.Generic.List[string]
    $logDir = Join-Path $Root "logs"
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    }
    $logPath = Join-Path $logDir "doctor.log"
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    Write-DoctorLine "=== Video Pipeline Studio — диагностика $ts ===" $lines
    Write-DoctorLine "Папка: $Root" $lines
    Write-DoctorLine "" $lines

    if (Get-Command git -ErrorAction SilentlyContinue) {
        $branch = (git -C $Root rev-parse --abbrev-ref HEAD 2>$null).Trim()
        $head = (git -C $Root rev-parse --short HEAD 2>$null).Trim()
        $dirty = git -C $Root status --porcelain 2>$null
        Write-DoctorLine "Git: ветка=$branch  коммит=$head" $lines
        if ($dirty) {
            Write-DoctorLine "Git: есть незакоммиченные изменения ($(@($dirty).Count) файл(ов))" $lines
        } else {
            Write-DoctorLine "Git: рабочая копия чистая" $lines
        }
    } else {
        Write-DoctorLine "Git: НЕ НАЙДЕН" $lines
    }

    $vf = Join-Path $Root "web\STUDIO_VERSION"
    if (Test-Path $vf) {
        $vl = @(Get-Content -LiteralPath $vf -Encoding UTF8 | Select-Object -First 2)
        Write-DoctorLine "STUDIO_VERSION: v$($vl[0])  sha=$($vl[1])" $lines
    } else {
        Write-DoctorLine "STUDIO_VERSION: отсутствует" $lines
    }

    $idx = Join-Path $Root "web\out\index.html"
    Write-DoctorLine "web/out/index.html: $(if (Test-Path $idx) { 'OK' } else { 'ОТСУТСТВУЕТ' })" $lines

    foreach ($port in @(8765, 29229)) {
        try {
            $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop | Select-Object -First 1
            Write-DoctorLine "Порт ${port}: ЗАНЯТ (PID $($c.OwningProcess))" $lines
        } catch {
            Write-DoctorLine "Порт ${port}: свободен" $lines
        }
    }

    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $py) {
        $ver = & $py --version 2>&1
        Write-DoctorLine "Python venv: OK ($ver)" $lines
        & $py -c "import fastapi, sqlalchemy, playwright" 2>$null
        Write-DoctorLine "Импорты fastapi/sqlalchemy/playwright: $(if ($LASTEXITCODE -eq 0) { 'OK' } else { 'ОШИБКА' })" $lines
    } else {
        Write-DoctorLine "Python venv: ОТСУТСТВУЕТ (.venv)" $lines
    }

    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-DoctorLine "Node.js: $((node -v 2>&1))" $lines
    } else {
        Write-DoctorLine "Node.js: НЕ НАЙДЕН" $lines
    }

    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-DoctorLine "FFmpeg: $((ffmpeg -version 2>&1 | Select-Object -First 1))" $lines
    } else {
        Write-DoctorLine "FFmpeg: НЕ НАЙДЕН в PATH" $lines
    }

    foreach ($path in @("data", "prompts", "logs", ".env")) {
        $full = Join-Path $Root $path
        Write-DoctorLine "Защищённый путь ${path}: $(if (Test-Path $full) { 'есть' } else { 'нет' })" $lines
    }

    if (Test-StudioHealth) {
        Write-DoctorLine "Бэкенд :8765/api/health: OK" $lines
        try {
            $sv = Invoke-RestMethod "http://127.0.0.1:8765/api/studio-version" -TimeoutSec 5
            Write-DoctorLine "API studio-version: $($sv.label)" $lines
        } catch {
            Write-DoctorLine "API studio-version: недоступен" $lines
        }
    } else {
        Write-DoctorLine "Бэкенд :8765/api/health: не отвечает" $lines
    }

    Write-DoctorLine "" $lines
    Write-DoctorLine "Лог сохранён: logs\doctor.log" $lines

    $header = "=== $ts ==="
    Add-Content -Path $logPath -Value $header -Encoding UTF8
    $lines | ForEach-Object { Add-Content -Path $logPath -Value $_ -Encoding UTF8 }
    Add-Content -Path $logPath -Value "" -Encoding UTF8
    return $true
}

function Show-StudioMenu {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Video Pipeline Studio" -ForegroundColor Cyan
    Write-Host "  $Root" -ForegroundColor DarkGray
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] Запустить студию (backend + web + браузер)"
    Write-Host "  [2] Обновить и запустить (git origin/main + зависимости + запуск)"
    Write-Host "  [3] Починить установку (pip, web, Playwright, FFmpeg)"
    Write-Host "  [4] Диагностика (версия, git, порты, logs/doctor.log)"
    Write-Host "  [0] Выход"
    Write-Host ""
}

# --- main ---
if (-not (Test-RepoRoot)) {
    Invoke-StudioPause
    exit 1
}

$ok = $true
if ($Action -eq "1") {
    $ok = Invoke-StudioStart
} elseif ($Action -eq "2") {
    $ok = Invoke-StudioUpdateAndStart
} elseif ($Action -eq "3") {
    $ok = Invoke-StudioRepair
} elseif ($Action -eq "4") {
    $ok = Invoke-StudioDoctor
} elseif ($Action -eq "") {
    while ($true) {
        Show-StudioMenu
        $choice = Read-Host "Выберите пункт"
        switch ($choice) {
            "1" { $ok = Invoke-StudioStart; if (-not $ok) { Invoke-StudioPause } }
            "2" { $ok = Invoke-StudioUpdateAndStart; if (-not $ok) { Invoke-StudioPause } }
            "3" { $ok = Invoke-StudioRepair; if (-not $ok) { Invoke-StudioPause } }
            "4" { $ok = Invoke-StudioDoctor; Invoke-StudioPause }
            "0" { break }
            default {
                Write-StudioMsg "Неизвестный пункт: $choice" "Yellow"
                Invoke-StudioPause
            }
        }
        if ($choice -eq "0") { break }
    }
    exit 0
} else {
    Write-StudioMsg "Неизвестный параметр: $Action" "Red"
    $ok = $false
}

if (-not $ok) {
    Invoke-StudioPause
    exit 1
}
exit 0
