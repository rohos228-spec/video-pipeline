# Единый лаунчер Video Pipeline Studio (меню на русском)
# Вызывается из STUDIO.cmd в корне репозитория.

param(
    [ValidateSet("1", "2", "3", "4", "5", "6", "P", "p", "")]
    [string]$Action = ""
)

$ErrorActionPreference = "Continue"
$StudioBranch = "main"
if ($env:VP_REPO_ROOT) {
    $Root = $env:VP_REPO_ROOT.TrimEnd('\', '/')
} elseif ($PSScriptRoot) {
    $Root = Split-Path -Parent $PSScriptRoot
} else {
    $Root = (Get-Location).Path
}
Set-Location -LiteralPath $Root

$VpProfileScript = Join-Path $Root "scripts\VpBrowserProfile.ps1"
if (Test-Path $VpProfileScript) {
    . $VpProfileScript
}

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

function Test-PortListening {
    param([int]$Port)
    try {
        $null = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Stop-StudioBackend {
    $stop = Join-Path $Root "scripts\stop-backend.ps1"
    if (Test-Path $stop) {
        & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet -WaitSec 5 2>$null
    }
    Start-Sleep -Seconds 1
}

function Open-VpAiTabs {
    if (-not (Get-Command Find-VpChromeExe -ErrorAction SilentlyContinue)) {
        return
    }
    $chrome = Find-VpChromeExe
    if (-not $chrome) { return }
    $profile = Get-VpBrowserUserDataDir
    foreach ($url in @("https://outsee.io/", "https://chatgpt.com/")) {
        try {
            Start-Process -FilePath $chrome -ArgumentList @(
                "--user-data-dir=$profile",
                $url
            )
        } catch { }
    }
}

function Ensure-StudioChromeCdp {
    if (-not (Get-Command Test-VpChromeCdp -ErrorAction SilentlyContinue)) {
        Write-StudioMsg "VpBrowserProfile.ps1 не загружен — Chrome CDP пропущен." "Yellow"
        return $false
    }
    if (Test-VpChromeCdp -Port 29229) {
        return $true
    }
    try {
        Start-VpChromeCdp -Port 29229 -SkipCloseCheck
        return $true
    } catch {
        Write-StudioMsg "Chrome CDP :29229 не запустился — $($_.Exception.Message)" "Yellow"
        return $false
    }
}

function Invoke-StudioBrowserAi {
    Write-StudioMsg "=== [3] Браузер с ИИ ===" "Cyan"
    if (-not (Get-Command Test-VpChromeCdp -ErrorAction SilentlyContinue)) {
        Write-StudioMsg "ОШИБКА: scripts\VpBrowserProfile.ps1 не найден." "Red"
        return $false
    }
    $profile = Get-VpBrowserUserDataDir
    Write-StudioMsg "Профиль Chrome: $profile" "DarkGray"
    if (Test-VpChromeCdp -Port 29229) {
        Write-StudioMsg "Браузер с ИИ уже запущен (CDP :29229) — второе окно не открываю." "Green"
        Open-VpAiTabs
        return $true
    }
    try {
        Start-VpChromeCdp -Port 29229 -SkipCloseCheck
        Start-Sleep -Seconds 1
        Open-VpAiTabs
        Write-StudioMsg "OK: Chrome CDP :29229 запущен, вкладки outsee.io и chatgpt.com открыты." "Green"
        return $true
    } catch {
        Write-StudioMsg "ОШИБКА: $($_.Exception.Message)" "Red"
        if (Get-Command Show-VpChromeDiagnostics -ErrorAction SilentlyContinue) {
            Show-VpChromeDiagnostics -Port 29229
        }
        return $false
    }
}

function Invoke-StudioStop {
    Write-StudioMsg "=== [2] Остановить всё ===" "Cyan"
    Write-StudioMsg "Останавливаю только бэкенд Studio (Chrome с ИИ не трогаю)." "DarkGray"
    $stop = Join-Path $Root "scripts\stop-backend.ps1"
    if (-not (Test-Path $stop)) {
        Write-StudioMsg "ОШИБКА: не найден scripts\stop-backend.ps1" "Red"
        return $false
    }
    & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -WaitSec 15
    if (-not (Test-PortListening -Port 8765)) {
        Write-StudioMsg "Студия остановлена (порт 8765 свободен)." "Green"
        return $true
    }
    Write-StudioMsg "ОШИБКА: порт 8765 всё ещё занят после 15 с. Закройте окно бэкенда вручную." "Red"
    return $false
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
    Ensure-StudioChromeCdp | Out-Null
}

function Start-StudioBackendWindow {
    $rb = Join-Path $Root "scripts\run-backend.ps1"
    if (-not (Test-Path $rb)) {
        Write-StudioMsg "ОШИБКА: не найден scripts\run-backend.ps1" "Red"
        return $false
    }
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-StudioMsg "ОШИБКА: .venv не найден. Запустите install.ps1 или пункт [5]." "Red"
        return $false
    }
    & $py -c 'from app.web.api import create_app; create_app()' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ОШИБКА: Python create_app() не прошёл. Попробуйте [5] Починить установку." "Red"
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
        Write-StudioMsg "ВНИМАНИЕ: web/out отсутствует. Сначала [5] Починить установку." "Yellow"
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

function Invoke-StudioPromptMigrate {
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: .venv не найден — миграция промтов пропущена." "Yellow"
        return $false
    }
    Write-StudioMsg "==> Миграция пользовательских промтов → data/prompts/ ..." "Cyan"
    & $py -m app.services.prompt_migrate 2>&1 | ForEach-Object { Write-StudioMsg $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: миграция промтов завершилась с ошибкой." "Yellow"
        return $false
    }
    Write-StudioMsg "OK: пользовательские промты в data/prompts/ (git reset их не трогает)." "Green"
    return $true
}

function Invoke-StudioRestorePromptsFromStash {
    Write-StudioMsg "=== [P] Восстановить промты из автосохранений ===" "Cyan"
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-StudioMsg "ОШИБКА: .venv не найден." "Red"
        return $false
    }
    & $py -m app.services.prompt_stash_restore 2>&1 | ForEach-Object { Write-StudioMsg $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ОШИБКА: восстановление промтов не удалось." "Red"
        return $false
    }
    Write-StudioMsg "OK: stash не удалены — остаются как резерв." "Green"
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
    Write-StudioMsg "    (data/ в .gitignore — не затрагивается reset; prompts/ в репо — reset откатит tracked-файлы)" "DarkGray"
    Write-StudioMsg "    Пользовательские промты хранятся в data/prompts/ и сохраняются при обновлении." "DarkGray"
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
    Write-StudioMsg "=== [4] Обновить и запустить (origin/$StudioBranch) ===" "Cyan"
    Invoke-StudioPromptMigrate | Out-Null
    Invoke-StudioGitStash | Out-Null
    if (-not (Invoke-StudioGitUpdate)) {
        return $false
    }
    # После обновления кода — подтянуть промты из прошлых studio-stash (если есть).
    $pyAfter = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $pyAfter) {
        Write-StudioMsg "==> Восстановление промтов из studio-stash → data/prompts/ ..." "Cyan"
        & $pyAfter -m app.services.prompt_stash_restore 2>&1 | ForEach-Object { Write-StudioMsg $_ }
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
        Write-StudioMsg "ВНИМАНИЕ: web/out отсутствует после обновления — запускаю [5] сборку UI..." "Yellow"
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
    Write-StudioMsg "=== [5] Починить установку ===" "Cyan"
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
    Write-StudioMsg "=== [6] Диагностика ===" "Cyan"
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

    if (Get-Command Get-VpBrowserUserDataDir -ErrorAction SilentlyContinue) {
        $prof = Get-VpBrowserUserDataDir
        $cdpOk = $false
        if (Get-Command Test-VpChromeCdp -ErrorAction SilentlyContinue) {
            $cdpOk = Test-VpChromeCdp -Port 29229
        }
        Write-DoctorLine "Chrome профиль (CDP): $prof" $lines
        Write-DoctorLine "Chrome CDP :29229: $(if ($cdpOk) { 'OK' } else { 'не отвечает' })" $lines
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

    foreach ($path in @("data", "data/prompts", "prompts", "logs", ".env")) {
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
    Write-Host "  [1] Запустить студию (бэкенд + Chrome CDP + http://127.0.0.1:8765)"
    Write-Host "  [2] Остановить всё (бэкенд :8765; Chrome с ИИ не закрывать)"
    Write-Host "  [3] Браузер с ИИ (Chrome CDP :29229, outsee.io + chatgpt.com)"
    Write-Host "  [4] Обновить и запустить (git origin/main + зависимости + запуск)"
    Write-Host "  [5] Починить установку (pip, web, Playwright, FFmpeg)"
    Write-Host "  [6] Диагностика (версия, git, порты, logs/doctor.log)"
    Write-Host "  [P] Восстановить промты из автосохранений (git stash studio)"
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
    $ok = Invoke-StudioStop
} elseif ($Action -eq "3") {
    $ok = Invoke-StudioBrowserAi
} elseif ($Action -eq "P" -or $Action -eq "p") {
    $ok = Invoke-StudioRestorePromptsFromStash
} elseif ($Action -eq "4") {
    $ok = Invoke-StudioUpdateAndStart
} elseif ($Action -eq "5") {
    $ok = Invoke-StudioRepair
} elseif ($Action -eq "6") {
    $ok = Invoke-StudioDoctor
} elseif ($Action -eq "") {
    while ($true) {
        Show-StudioMenu
        $choice = Read-Host "Выберите пункт"
        switch ($choice) {
            "1" { $ok = Invoke-StudioStart; if (-not $ok) { Invoke-StudioPause } }
            "2" { $ok = Invoke-StudioStop; if (-not $ok) { Invoke-StudioPause } }
            "3" { $ok = Invoke-StudioBrowserAi; if (-not $ok) { Invoke-StudioPause } }
            "4" { $ok = Invoke-StudioUpdateAndStart; if (-not $ok) { Invoke-StudioPause } }
            "5" { $ok = Invoke-StudioRepair; if (-not $ok) { Invoke-StudioPause } }
            "6" { $ok = Invoke-StudioDoctor; Invoke-StudioPause }
            { $_ -eq "P" -or $_ -eq "p" } { $ok = Invoke-StudioRestorePromptsFromStash; if (-not $ok) { Invoke-StudioPause } }
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
