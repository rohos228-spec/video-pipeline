<#
Video Pipeline Studio Launcher
#>
param(
    [ValidateSet("1", "2", "3", "4", "5", "6", "7", "")]
    [string]$Action = ""
)

# Единый лаунчер Video Pipeline Studio (меню на русском)
# Вызывается из STUDIO.cmd в корне репозитория.

$ErrorActionPreference = "Continue"
if ($env:VP_REPO_ROOT) {
    $Root = $env:VP_REPO_ROOT.TrimEnd('\', '/')
} elseif ($PSScriptRoot) {
    $Root = Split-Path -Parent $PSScriptRoot
} else {
    $Root = (Get-Location).Path
}
Set-Location -LiteralPath $Root

# Ветки (выбор при первом запуске / [5] -> только data/studio-pc-branch).
# Лаунчер никогда не пишет .env.
# [4] всегда тянет origin/<сохранённая>, не хардкод main.
$script:PcBranches = @("housepc", "tompc", "strangepc", "workpc", "main")
$script:PcBranchFile = Join-Path $Root "data\studio-pc-branch"
$EnvFile = Join-Path $Root ".env"
$StudioBranch = ""

function Test-StudioPcBranchName {
    param([string]$Name)
    return ($script:PcBranches -contains $Name)
}

function Read-StudioPcBranchFromEnv {
    if ($env:ORCHESTRATOR_GIT_BRANCH -and (Test-StudioPcBranchName $env:ORCHESTRATOR_GIT_BRANCH)) {
        return $env:ORCHESTRATOR_GIT_BRANCH
    }
    if (Test-Path -LiteralPath $EnvFile) {
        foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8 -ErrorAction SilentlyContinue) {
            if ($line -match '^\s*ORCHESTRATOR_GIT_BRANCH\s*=\s*(.+)\s*$') {
                $cand = $Matches[1].Trim().Trim('"').Trim("'")
                if (Test-StudioPcBranchName $cand) { return $cand }
                break
            }
        }
    }
    return ""
}

function Save-StudioPcBranch {
    param([Parameter(Mandatory = $true)][string]$Branch)
    if (-not (Test-StudioPcBranchName $Branch)) { return $false }
    $dataDir = Join-Path $Root "data"
    if (-not (Test-Path -LiteralPath $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    }
    Set-Content -LiteralPath $script:PcBranchFile -Value $Branch -Encoding UTF8 -NoNewline
    $env:ORCHESTRATOR_GIT_BRANCH = $Branch
    $script:StudioBranch = $Branch
    return $true
}

function Get-StudioPcBranch {
    if (Test-Path -LiteralPath $script:PcBranchFile) {
        $raw = (Get-Content -LiteralPath $script:PcBranchFile -Encoding UTF8 -TotalCount 1 -ErrorAction SilentlyContinue)
        if ($null -ne $raw) {
            $cand = "$raw".Trim()
            if (Test-StudioPcBranchName $cand) { return $cand }
        }
    }
    $fromEnv = Read-StudioPcBranchFromEnv
    if ($fromEnv) {
        # Миграция: ветка из .env / процесса -> только data/studio-pc-branch, .env не трогаем.
        Save-StudioPcBranch -Branch $fromEnv | Out-Null
        return $fromEnv
    }
    return ""
}

function Show-StudioBranchPicker {
    param([switch]$AllowCancel)
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Выберите ветку этого ПК" -ForegroundColor Cyan
    Write-Host "  (сохранится; смена ветки - [5], обновление кода - [4])" -ForegroundColor DarkGray
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    for ($i = 0; $i -lt $script:PcBranches.Count; $i++) {
        $n = $i + 1
        Write-Host ("  [{0}] {1}" -f $n, $script:PcBranches[$i])
    }
    if ($AllowCancel) {
        Write-Host "  [0] Отмена"
    }
    Write-Host ""
    while ($true) {
        $choice = Read-Host "Номер ветки"
        if ($AllowCancel -and $choice -eq "0") { return "" }
        if ($choice -match '^[1-5]$') {
            $idx = [int]$choice - 1
            $br = $script:PcBranches[$idx]
            if (Save-StudioPcBranch -Branch $br) {
                Write-StudioMsg "OK: ветка сохранена - $br ([4] будет тянуть origin/$br)" "Green"
                return $br
            }
        }
        Write-StudioMsg "Выберите 1-5 (housepc / tompc / strangepc / workpc / main)." "Yellow"
    }
}

function Ensure-StudioPcBranchSelected {
    param([switch]$InteractiveRequired)
    $br = Get-StudioPcBranch
    if ($br) {
        $script:StudioBranch = $br
        return $true
    }
    if (-not $InteractiveRequired) {
        Write-StudioMsg "ОШИБКА: ветка ПК не выбрана. Запустите STUDIO.cmd без аргументов и выберите ветку (1-4)." "Red"
        return $false
    }
    $picked = Show-StudioBranchPicker
    return [bool]$picked
}

function Invoke-StudioBranchHub {
    Write-StudioMsg "=== [5] Ветка ПК ===" "Cyan"
    if (-not (Ensure-StudioPcBranchSelected -InteractiveRequired)) {
        return $false
    }
    $br = $script:StudioBranch
    Write-Host ""
    Write-Host "  Текущая ветка: $br" -ForegroundColor Green
    Write-Host "  Обновление кода - пункт [4] (origin/$br)" -ForegroundColor DarkGray
    Write-Host "  [1] Сменить ветку ПК"
    Write-Host "  [0] Назад"
    Write-Host ""
    $sub = Read-Host "Выберите"
    switch ($sub) {
        "1" {
            $null = Show-StudioBranchPicker -AllowCancel
            return $true
        }
        "0" { return $true }
        default {
            Write-StudioMsg "Неизвестный пункт: $sub" "Yellow"
            return $true
        }
    }
}

$script:StudioBranch = Get-StudioPcBranch
# Совместимость со старым кодом, читающим $StudioBranch
$StudioBranch = $script:StudioBranch

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
        Write-StudioMsg "VpBrowserProfile.ps1 не загружен - Chrome CDP пропущен." "Yellow"
        return $false
    }
    if (Test-VpChromeCdp -Port 29229) {
        return $true
    }
    try {
        Start-VpChromeCdp -Port 29229 -SkipCloseCheck
        return $true
    } catch {
        Write-StudioMsg "Chrome CDP :29229 не запустился - $($_.Exception.Message)" "Yellow"
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
        Write-StudioMsg "Браузер с ИИ уже запущен (CDP :29229) - второе окно не открываю." "Green"
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
        Write-StudioMsg "ОШИБКА: .venv не найден. Запустите install.ps1 или пункт [6]." "Red"
        return $false
    }
    Set-StudioNvidiaEnv
    # Не глотать stderr и не pipe в Out-Null: в PS 5.1 $LASTEXITCODE после pipe
    # часто null, а ($null -ne 0) = $true -> ложный FAIL create_app.
    # Одна строка + одинарные кавычки: multiline here-string в `python -c`
    # на Win/PS 5.1 ломает print("...") → SyntaxError: '(' was never closed.
    $preflightPy = "import app.bootstrap_env; from app.web.api import create_app; create_app(); print('create_app OK')"
    $preflightOut = @(& $py -c $preflightPy 2>&1)
    $preflightCode = if ($null -eq $LASTEXITCODE) { 1 } else { [int]$LASTEXITCODE }
    $preflightOk = ($preflightCode -eq 0) -and ($preflightOut -match "create_app OK")
    if (-not $preflightOk) {
        Write-StudioMsg "ОШИБКА: Python create_app() не прошёл. Попробуйте [6] Починить установку." "Red"
        $preflightOut | ForEach-Object { Write-StudioMsg "  $_" "Red" }
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
    Write-StudioMsg "Таймаут ожидания :8765 - откройте http://127.0.0.1:8765 вручную" "Yellow"
    return $false
}

function Get-StudioPython {
    $candidates = @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $Root ".venv\bin\python"),
        "python",
        "py",
        "python3"
    )
    foreach ($c in $candidates) {
        if ($c -eq "py") {
            $cmd = Get-Command py -ErrorAction SilentlyContinue
            if ($cmd) { return @("py", "-3") }
            continue
        }
        if ($c -match '[\\/]' -or $c.EndsWith(".exe")) {
            if (Test-Path -LiteralPath $c) { return @($c) }
            continue
        }
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { return @($c) }
    }
    return $null
}

function Invoke-StudioPythonHelper {
    param(
        [Parameter(Mandatory = $true)][string[]]$HelperArgs
    )
    $pyArgs = @(Get-StudioPython)
    if (-not $pyArgs -or $pyArgs.Count -lt 1) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: Python не найден - не могу защитить/вернуть prompts/." "Yellow"
        return $false
    }
    $helperPy = Join-Path $Root "scripts\return_prompts_from_stash.py"
    if (-not (Test-Path -LiteralPath $helperPy)) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: нет scripts\return_prompts_from_stash.py" "Yellow"
        return $false
    }
    $exe = $pyArgs[0]
    $prefix = @()
    if ($pyArgs.Count -gt 1) { $prefix = $pyArgs[1..($pyArgs.Count - 1)] }
    # UTF-8: иначе на cp1251 Windows helper/git сыпет UnicodeDecodeError в консоль.
    $prevPyIo = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    try {
        & $exe @prefix $helperPy @HelperArgs 2>&1 | ForEach-Object { Write-StudioMsg $_ }
        return ($LASTEXITCODE -eq 0)
    } finally {
        if ($null -eq $prevPyIo) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
        else { $env:PYTHONIOENCODING = $prevPyIo }
    }
}

function Invoke-StudioBackupPromptsAside {
    Write-StudioMsg "==> Снимок prompts/ вне репо (LOCALAPPDATA/TEMP) перед update..." "Cyan"
    return (Invoke-StudioPythonHelper -HelperArgs @("--repo", $Root, "--backup-aside", "--json"))
}

function Invoke-StudioRestorePromptsAside {
    Write-StudioMsg "==> Возвращаю user-промты из снимка вне репо..." "Cyan"
    return (Invoke-StudioPythonHelper -HelperArgs @("--repo", $Root, "--restore-aside", "--json"))
}

function Invoke-StudioRecoverPromptsFromAllStashes {
    # Безопасный возврат prompts/*: aside + studio stash (идемпотентно).
    $helperPy = Join-Path $Root "scripts\return_prompts_from_stash.py"
    if (Test-Path -LiteralPath $helperPy) {
        $pyArgs = @(Get-StudioPython)
        if ($pyArgs -and $pyArgs.Count -ge 1) {
            $exe = $pyArgs[0]
            $prefix = if ($pyArgs.Count -gt 1) { $pyArgs[1..($pyArgs.Count - 1)] } else { @() }
            & $exe @prefix $helperPy --repo $Root --startup-once --json 2>&1 | Out-Null
        }
    }
}

function Invoke-StudioStart {
    Write-StudioMsg "=== [1] Запуск студии ===" "Cyan"
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-StudioMsg "ВНИМАНИЕ: web/out отсутствует. Сначала [6] Починить установку." "Yellow"
    }
    Invoke-StudioRecoverPromptsFromAllStashes
    Stop-StudioBackend
    Set-StudioNvidiaEnv
    Invoke-StudioPredownloadNemo | Out-Null
    # Запуск бэкенда и открытие веб-интерфейса в браузере (все сервисы работают через прямой HTTP API)
    if (-not (Start-StudioBackendWindow)) {
        return $false
    }
    Open-StudioBrowser
    Write-StudioMsg "Студия: http://127.0.0.1:8765 (Ctrl+F5 в браузере)" "Green"
    return $true
}

function Test-StudioPromptsDirty {
    $porcelain = @(git -C $Root status --porcelain -- prompts 2>$null)
    return ($porcelain.Count -gt 0)
}

function Invoke-StudioGitStash {
    # Returns stash ref (e.g. stash@{0}) when a stash was created; otherwise $null.
    # On failure returns the string "FAILED" so caller can abort if prompts are dirty.
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-StudioMsg "ОШИБКА: git не найден в PATH." "Red"
        return "FAILED"
    }
    $status = git -C $Root status --porcelain 2>&1
    if (-not $status) {
        Write-StudioMsg "Локальных изменений нет - stash не нужен." "Gray"
        return $null
    }
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $msg = "studio: автосохранение перед обновлением $stamp"
    Write-StudioMsg "==> Сохраняю локальные изменения в git stash..." "Cyan"
    Write-StudioMsg "    (data/, logs/, .env, prompts/ в .gitignore - не затрагиваются reset)" "DarkGray"
    git -C $Root stash push -u -m $msg 2>&1 | ForEach-Object { Write-StudioMsg $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioMsg "ОШИБКА: git stash не удался." "Red"
        return "FAILED"
    }
    Write-StudioMsg "OK: stash создан (stash@{0})." "Green"
    return 'stash@{0}'
}

function Invoke-StudioReturnPromptEditsFromStash {
    # После reset --hard код с origin; локальные правки prompts/ - из stash.
    param([string]$StashRef)
    if ([string]::IsNullOrWhiteSpace($StashRef) -or $StashRef -eq "FAILED") { return }

    # Важно: stash@{0} всегда в кавычках - иначе PowerShell ест @{0} как splat.
    Write-StudioMsg "==> Возвращаю локальные правки prompts/ из '$StashRef' ..." "Cyan"
    $ok = Invoke-StudioPythonHelper -HelperArgs @("--repo", $Root, "--stash", "$StashRef", "--json")
    if ($ok) { return }

    $helperPs1 = Join-Path $Root "scripts\Return-PromptsFromStash.ps1"
    if (Test-Path -LiteralPath $helperPs1) {
        & $helperPs1 -Root $Root -StashRef $StashRef
        return
    }
}

function Invoke-StudioGitUpdate {
    # Всегда перечитать сохранённую ветку (data/studio-pc-branch / .env) - не main по умолчанию
    $saved = Get-StudioPcBranch
    if ($saved) { $script:StudioBranch = $saved }
    $StudioBranch = $script:StudioBranch
    if (-not (Test-StudioPcBranchName $StudioBranch)) {
        Write-StudioMsg "ОШИБКА: ветка не задана. Пункт [5] - выберите housepc/tompc/strangepc/workpc/main." "Red"
        return $false
    }
    Write-StudioMsg "==> обновление с сохранённой ветки: origin/$StudioBranch" "Cyan"
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

function Test-StudioAsrBackendNvidia {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        $match = Select-String -Path $envFile -Pattern '^\s*ASR_BACKEND\s*=\s*(\S+)' | Select-Object -First 1
        if ($match) {
            $val = $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
            return ($val.ToLower() -eq "nvidia")
        }
    }
    return $true
}

function Set-StudioNvidiaEnv {
    if (-not (Test-StudioAsrBackendNvidia)) { return }
    $cacheRoot = Join-Path $Root "data\.cache"
    $cacheTemp = Join-Path $cacheRoot "temp"
    $cacheHf = Join-Path $cacheRoot "huggingface"
    $cacheHfHub = Join-Path $cacheHf "hub"
    $cacheNemo = Join-Path $cacheRoot "nemo"
    foreach ($d in @($cacheRoot, $cacheTemp, $cacheHf, $cacheHfHub, $cacheNemo)) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    }
    $env:TEMP = $cacheTemp
    $env:TMP = $cacheTemp
    $env:TMPDIR = $cacheTemp
    $env:HF_HOME = $cacheHf
    $env:HUGGINGFACE_HUB_CACHE = $cacheHfHub
    $env:TRANSFORMERS_CACHE = $cacheHfHub
    $env:NEMO_CACHE_DIR = $cacheNemo
    $env:HF_HUB_DISABLE_XET = "1"
    $env:HF_HUB_DISABLE_SYMLINKS = "1"
    $env:HF_HUB_ENABLE_HF_TRANSFER = "0"
    $env:HF_HUB_DOWNLOAD_TIMEOUT = "600"
    $env:HF_HUB_ETAG_TIMEOUT = "60"
    $env:HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY = "1"
    $env:TOKENIZERS_PARALLELISM = "false"
}

function Get-StudioNvidiaAsrModel {
    $default = "nvidia/parakeet-tdt-0.6b-v3"
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return $default }
    $match = Select-String -Path $envFile -Pattern '^\s*NVIDIA_ASR_MODEL\s*=\s*(\S+)' | Select-Object -First 1
    if (-not $match) { return $default }
    return $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
}

function Invoke-StudioPredownloadNemo {
    if (-not (Test-StudioAsrBackendNvidia)) { return $true }
    Set-StudioNvidiaEnv
    $model = Get-StudioNvidiaAsrModel
    $slug = ($model -replace "/", "--")
    $nemoDir = Join-Path $Root "data\.cache\nemo"
    $dest = Join-Path $nemoDir "$slug.nemo"
    $part = "$dest.part"
    if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt 50000000)) {
        Write-StudioMsg "OK: NeMo модель уже на диске ($slug.nemo)." "Green"
        return $true
    }
    $fileName = switch ($model) {
        "nvidia/parakeet-tdt-0.6b-v3" { "parakeet-tdt-0.6b-v3.nemo" }
        "nvidia/parakeet-tdt-0.6b-v2" { "parakeet-tdt-0.6b-v2.nemo" }
        default {
            if ($model -match '\.nemo$') { Split-Path $model -Leaf }
            else { "$(Split-Path $model -Leaf).nemo" }
        }
    }
    $url = "https://huggingface.co/$model/resolve/main/$fileName"
    if (-not (Test-Path $nemoDir)) { New-Item -ItemType Directory -Force -Path $nemoDir | Out-Null }
    Write-StudioMsg "==> Скачивание $fileName (~2.5 GB) через curl, без Python/HF temp..." "Cyan"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: curl.exe не найден - скачает Python при старте." "Yellow"
        return $true
    }
    & curl.exe -L -C - -o $part $url
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $part)) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: curl не докачал модель - повторит Python." "Yellow"
        return $true
    }
    if ((Get-Item $part).Length -lt 50000000) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: файл слишком мал - повторит Python." "Yellow"
        return $true
    }
    Move-Item -Force -Path $part -Destination $dest
    Write-StudioMsg "OK: $fileName сохранён в data\.cache\nemo\" "Green"
    return $true
}

function Invoke-StudioNvidiaDeps {
    if (-not (Test-StudioAsrBackendNvidia)) {
        Write-StudioMsg "ASR_BACKEND не nvidia - пропуск NeMo." "DarkGray"
        return $true
    }
    Set-StudioNvidiaEnv
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { return $false }
    & $py -m pip uninstall -y hf-xet hf_xet 2>$null | Out-Null
    & $py -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('nemo.collections.asr') else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-StudioMsg "OK: NVIDIA NeMo ASR (Parakeet) установлен." "Green"
        return $true
    }
    Write-StudioMsg "==> pip install -e .[nvidia] (Parakeet ASR для таймкодов)..." "Cyan"
    $spec = (Resolve-Path -LiteralPath $Root).Path
    $env:PIP_DEFAULT_TIMEOUT = "600"
    Push-Location -LiteralPath $Root
    & $py -m pip install --default-timeout=600 -e "${spec}[nvidia]"
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) {
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: pip [nvidia] не удался - ASR fallback на whisper." "Yellow"
        return $true
    }
    Write-StudioMsg "OK: NVIDIA NeMo ASR установлен." "Green"
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
    if (-not (Ensure-StudioPcBranchSelected -InteractiveRequired)) {
        return $false
    }
    $StudioBranch = Get-StudioPcBranch
    if (-not $StudioBranch) { $StudioBranch = $script:StudioBranch }
    $script:StudioBranch = $StudioBranch
    Write-StudioMsg "=== [4] Обновить и запустить (origin/$StudioBranch) ===" "Cyan"
    # 1) Снимок prompts/ ВНЕ репо - главный предохранитель (stash может сдохнуть).
    Invoke-StudioBackupPromptsAside | Out-Null
    $promptsDirty = Test-StudioPromptsDirty
    $stashRef = Invoke-StudioGitStash
    if ($stashRef -eq "FAILED") {
        if ($promptsDirty) {
            Write-StudioMsg "СТОП: есть локальные правки в prompts/, stash не удался - обновление отменено, промты не трогаю." "Red"
            return $false
        }
        Write-StudioMsg "ПРЕДУПРЕЖДЕНИЕ: stash не удался, локальных правок prompts/ нет - продолжаю." "Yellow"
        $stashRef = $null
    }
    if (-not (Invoke-StudioGitUpdate)) {
        return $false
    }
    # 2) Вернуть из stash (если был)
    Invoke-StudioReturnPromptEditsFromStash -StashRef $stashRef
    # 3) Вернуть user-файлы из aside (даже если stash пустой/битый)
    Invoke-StudioRestorePromptsAside | Out-Null
    # 4) Подстраховка: старые studio-stash + aside ещё раз safe
    Invoke-StudioRecoverPromptsFromAllStashes
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
    if (-not (Invoke-StudioNvidiaDeps)) { return $false }
    Invoke-StudioPredownloadNemo | Out-Null
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-StudioMsg "ВНИМАНИЕ: web/out отсутствует после обновления - запускаю сборку UI..." "Yellow"
        if (-not (Invoke-StudioRepairWeb)) { return $false }
    }
    Stop-StudioBackend
    # После git reset в памяти может остаться старый studio.ps1 — стартуем файл с диска.
    $ps1 = if ($PSCommandPath) { $PSCommandPath } else { Join-Path $Root "scripts\studio.ps1" }
    $exe = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell.exe" }
    Write-StudioMsg "Запуск с диска (не из памяти старого лаунчера)" "DarkGray"
    & $exe -NoProfile -ExecutionPolicy Bypass -File $ps1 -Action 1
    return ($LASTEXITCODE -eq 0)
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
    Write-StudioMsg "=== [6] Починить установку ===" "Cyan"
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-StudioMsg "ОШИБКА: .venv не найден. Запустите install.ps1 для первичной установки." "Red"
        return $false
    }
    if (-not (Invoke-StudioPipInstall)) { return $false }
    if (-not (Invoke-StudioNvidiaDeps)) { return $false }
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
        Write-StudioMsg "OK: FFmpeg - $ff" "Green"
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

    Write-DoctorLine "=== Video Pipeline Studio - диагностика $ts ===" $lines
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

function Get-StudioLauncherStamp {
    try {
        return (git -C $Root rev-parse --short HEAD 2>$null).Trim()
    } catch {
        return "?"
    }
}

function Show-StudioMenu {
    $brLabel = if ($script:StudioBranch) { $script:StudioBranch } else { "не выбрана" }
    $stamp = Get-StudioLauncherStamp
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Video Pipeline Studio" -ForegroundColor Cyan
    Write-Host "  $Root" -ForegroundColor DarkGray
    Write-Host "  launcher $stamp | ветка ПК: $brLabel" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] Запустить студию (бэкенд API + веб-интерфейс http://127.0.0.1:8765)"
    Write-Host "  [2] Остановить всё (бэкенд :8765)"
    Write-Host "  [3] Открыть внешний браузер с ИИ (Chrome CDP :29229 — опционально)"
    Write-Host "  [4] Обновить и запустить (git origin/$brLabel + зависимости + запуск)"
    Write-Host "  [5] Ветка ПК ($brLabel): сменить"
    Write-Host "  [6] Починить установку (pip, web, Playwright, FFmpeg)"
    Write-Host "  [7] Диагностика (версия, git, порты, logs/doctor.log)"
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
} elseif ($Action -eq "4") {
    $ok = Invoke-StudioUpdateAndStart
} elseif ($Action -eq "5") {
    $ok = Invoke-StudioBranchHub
} elseif ($Action -eq "6") {
    $ok = Invoke-StudioRepair
} elseif ($Action -eq "7") {
    $ok = Invoke-StudioDoctor
} elseif ($Action -eq "") {
    if (-not (Ensure-StudioPcBranchSelected -InteractiveRequired)) {
        Invoke-StudioPause
        exit 1
    }
    while ($true) {
        Show-StudioMenu
        $choice = Read-Host "Выберите пункт"
        switch ($choice) {
            "1" { $ok = Invoke-StudioStart; if (-not $ok) { Invoke-StudioPause } }
            "2" { $ok = Invoke-StudioStop; if (-not $ok) { Invoke-StudioPause } }
            "3" { $ok = Invoke-StudioBrowserAi; if (-not $ok) { Invoke-StudioPause } }
            "4" { $ok = Invoke-StudioUpdateAndStart; if (-not $ok) { Invoke-StudioPause } }
            "5" { $ok = Invoke-StudioBranchHub; if (-not $ok) { Invoke-StudioPause } }
            "6" { $ok = Invoke-StudioRepair; if (-not $ok) { Invoke-StudioPause } }
            "7" { $ok = Invoke-StudioDoctor; Invoke-StudioPause }
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
