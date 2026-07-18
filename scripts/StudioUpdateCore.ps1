# PowerShell 5.1 - ASCII only (no em-dash / unicode quotes)
$script:StudioUpdateBranch = "devin/windows-installer"
$script:StudioUpdateCoreId = "studio-update-core-v9"

function Get-StudioRepoRoot {
    param([string]$StartDir = (Get-Location).Path)
    $dir = $StartDir
    for ($i = 0; $i -lt 12; $i++) {
        if (Test-Path (Join-Path $dir "pyproject.toml")) {
            return (Resolve-Path -LiteralPath $dir).Path
        }
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    return $null
}

function Write-StudioLog {
    param([string]$Message, [string]$Color = "Gray")
    Write-Host ("{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) -ForegroundColor $Color
}

function Restore-StudioWebUiFromGit {
    param([string]$Root)
    $br = $script:StudioUpdateBranch
    $outDir = Join-Path $Root "web\out"
    Write-StudioLog "> stop backend before web/out restore" "Cyan"
    Stop-StudioBackend $Root
    Start-Sleep -Seconds 1
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-StudioLog "> restore web/out + STUDIO_VERSION (try $attempt/3)" "Cyan"
        git -C $Root checkout "origin/$br" -- web/out web/STUDIO_VERSION 2>&1 | ForEach-Object { Write-StudioLog $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-StudioLog "git checkout web/out failed - delete folder and retry" "Yellow"
            if (Test-Path $outDir) {
                Remove-Item -LiteralPath $outDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            git -C $Root checkout "origin/$br" -- web/out web/STUDIO_VERSION 2>&1 | ForEach-Object { Write-StudioLog $_ }
        }
        if (Show-StudioVersionOnDisk -Root $Root) { return $true }
        Stop-StudioBackend $Root
        if (Test-Path $outDir) {
            Remove-Item -LiteralPath $outDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Invoke-StudioGit {
    param([string]$Root)
    Write-StudioLog "> git pull ($($script:StudioUpdateBranch))" "Cyan"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-StudioLog "FAIL: git not found" "Red"
        return $false
    }
    $br = $script:StudioUpdateBranch
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) {
        if (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
        elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
        else { $py = "python3" }
    }
    $pyHelper = Join-Path $Root "scripts\return_prompts_from_stash.py"
    $env:PYTHONIOENCODING = "utf-8"
    if (Test-Path -LiteralPath $pyHelper) {
        Write-StudioLog "> backup prompts/ aside (outside repo)" "Cyan"
        if ($py -eq "py") {
            & py -3 $pyHelper --repo $Root --backup-aside --json 2>&1 | ForEach-Object { Write-StudioLog $_ }
        } else {
            & $py $pyHelper --repo $Root --backup-aside --json 2>&1 | ForEach-Object { Write-StudioLog $_ }
        }
    }
    $promptsDirty = @((git -C $Root status --porcelain -- prompts 2>$null)).Count -gt 0
    $stashRef = $null
    $status = git -C $Root status --porcelain 2>&1
    if ($status) {
        $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $msg = "studio: автосохранение перед обновлением $stamp"
        Write-StudioLog "> git stash push -u ($msg)" "Cyan"
        git -C $Root stash push -u -m $msg 2>&1 | ForEach-Object { Write-StudioLog $_ }
        if ($LASTEXITCODE -ne 0) {
            if ($promptsDirty) {
                Write-StudioLog "FAIL: stash failed and prompts/ has local edits — abort (prompts kept)" "Red"
                return $false
            }
            Write-StudioLog "WARN: stash failed; no dirty prompts/ — continue" "Yellow"
        } else {
            $stashRef = 'stash@{0}'
        }
    }
    git -C $Root fetch origin $br 2>&1 | ForEach-Object { Write-StudioLog $_ }
    if ($LASTEXITCODE -ne 0) { return $false }
    git -C $Root reset --hard "origin/$br" 2>&1 | ForEach-Object { Write-StudioLog $_ }
    if ($LASTEXITCODE -ne 0) { return $false }
    git -C $Root checkout -B $br "origin/$br" 2>&1 | ForEach-Object { Write-StudioLog $_ }
    if (Test-Path -LiteralPath $pyHelper) {
        if ($stashRef) {
            Write-StudioLog "> return prompts/ from '$stashRef'" "Cyan"
            if ($py -eq "py") {
                & py -3 $pyHelper --repo $Root --stash "$stashRef" --json 2>&1 | ForEach-Object { Write-StudioLog $_ }
            } else {
                & $py $pyHelper --repo $Root --stash "$stashRef" --json 2>&1 | ForEach-Object { Write-StudioLog $_ }
            }
        }
        Write-StudioLog "> restore prompts/ from aside + stashes" "Cyan"
        if ($py -eq "py") {
            & py -3 $pyHelper --repo $Root --restore-aside --json 2>&1 | ForEach-Object { Write-StudioLog $_ }
            & py -3 $pyHelper --repo $Root --startup-once --json 2>&1 | ForEach-Object { Write-StudioLog $_ }
        } else {
            & $py $pyHelper --repo $Root --restore-aside --json 2>&1 | ForEach-Object { Write-StudioLog $_ }
            & $py $pyHelper --repo $Root --startup-once --json 2>&1 | ForEach-Object { Write-StudioLog $_ }
        }
    } elseif ($stashRef) {
        $helper = Join-Path $Root "scripts\Return-PromptsFromStash.ps1"
        if (Test-Path -LiteralPath $helper) {
            & $helper -Root $Root -StashRef $stashRef
        }
    }
    $uiOk = Restore-StudioWebUiFromGit -Root $Root
    Write-StudioLog "OK git $(git -C $Root rev-parse --short HEAD)" "Green"
    if (-not $uiOk) {
        Write-StudioLog "WARN: web/out stale - run STUDIO.cmd -> [3]" "Yellow"
    }
    return $true
}

function Show-StudioVersionOnDisk {
    param([string]$Root)
    $vf = Join-Path $Root "web\STUDIO_VERSION"
    if (-not (Test-Path $vf)) {
        Write-StudioLog "WARN: web/STUDIO_VERSION missing" "Yellow"
        return $false
    }
    $lines = @(Get-Content -LiteralPath $vf -Encoding UTF8 | Select-Object -First 2)
    $build = $lines[0]
    $sha = if ($lines.Count -gt 1) { $lines[1] } else { "?" }
    Write-StudioLog "STUDIO_VERSION on disk: v$build  $sha" "Green"
    $idx = Join-Path $Root "web\out\index.html"
    if (Test-Path $idx) {
        $m = Select-String -Path $idx -Pattern 'title="UI:\s*v(\d+)' -AllMatches -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($m -and $m.Matches.Count -gt 0) {
            $baked = $m.Matches[0].Groups[1].Value
            Write-StudioLog "web/out baked UI badge: v$baked" "Green"
            if ($baked -ne $build) {
                Write-StudioLog "WARN: baked UI (v$baked) != STUDIO_VERSION (v$build) - old web/out still on disk" "Yellow"
                return $false
            }
        }
    } else {
        Write-StudioLog "WARN: web/out/index.html missing" "Yellow"
        return $false
    }
    return $true
}

function Test-StudioPythonOk {
    param([string]$Root)
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) { return $false }
    & $py -c "import fastapi, sqlalchemy, playwright" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-StudioPipInstall {
    param([string]$Root)
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) {
        Write-StudioLog "FAIL: no .venv - run install.ps1 once" "Red"
        return $false
    }
    $spec = (Resolve-Path -LiteralPath $Root).Path
    $env:PIP_DEFAULT_TIMEOUT = "300"
    Write-StudioLog "> pip install -e $spec (slow net: wait)" "Cyan"
    for ($i = 1; $i -le 6; $i++) {
        if ($i -gt 1) {
            Write-StudioLog "pip retry $i/6 in 20 sec" "Yellow"
            Start-Sleep -Seconds 20
        }
        Push-Location -LiteralPath $Root
        & $py -m pip install --default-timeout=300 -e $spec
        $code = $LASTEXITCODE
        Pop-Location
        if ($code -eq 0) {
            Write-StudioLog "OK pip" "Green"
            return $true
        }
    }
    Write-StudioLog "FAIL pip - bad internet. Run CONTINUE-INSTALL.cmd later" "Red"
    return $false
}

function Stop-StudioBackend {
    param([string]$Root)
    $stop = Join-Path $Root "scripts\stop-backend.ps1"
    if (Test-Path $stop) {
        & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stop -Quiet 2>$null
    }
    Start-Sleep -Seconds 2
}

function Open-StudioBrowser {
    try { Start-Process "http://127.0.0.1:8765" } catch { }
}

function Test-StudioCreateApp {
    param([string]$Root)
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) { return $false }
    & $py -c "from app.web.api import create_app; create_app()" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Start-StudioBackendWindow {
    param([string]$Root)
    if (-not (Test-StudioCreateApp $Root)) {
        Write-StudioLog "FAIL: Python create_app() - backend will crash. git pull required." "Red"
        return $false
    }
    $rb = Join-Path $Root "scripts\run-backend.ps1"
    Write-StudioLog "Starting run-backend.ps1 window..." "Gray"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $rb -WorkingDirectory $Root
    $deadline = (Get-Date).AddSeconds(120)
    $started = Get-Date
    $lastWaitLog = -1
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:8765/api/health" -TimeoutSec 3 -UseBasicParsing
            if ($r.StatusCode -eq 200) {
                Write-StudioLog "OK backend" "Green"
                try {
                    $sv = Invoke-RestMethod "http://127.0.0.1:8765/api/studio-version" -TimeoutSec 5
                    Write-StudioLog "Version: $($sv.label)" "Green"
                } catch { }
                Open-StudioBrowser
                return $true
            }
        } catch { }
        Start-Sleep -Milliseconds 500
        $waitSec = [int]((Get-Date) - $started).TotalSeconds
        if ($waitSec -ge 10 -and ($waitSec % 10) -eq 0 -and $waitSec -ne $lastWaitLog) {
            Write-StudioLog "waiting for :8765 ... ${waitSec}s (see run-backend window)" "DarkGray"
            $lastWaitLog = $waitSec
        }
    }
    Write-StudioLog "Open manually: http://127.0.0.1:8765 (see run-backend window)" "Yellow"
    Open-StudioBrowser
    return $false
}

function Invoke-StudioUpdateOnly {
    param([string]$Root)
    Write-StudioLog "=== update only ($($script:StudioUpdateCoreId)) ===" "Cyan"
    if (-not (Invoke-StudioGit $Root)) { return $false }
    if (Test-StudioPythonOk $Root) {
        Write-StudioLog "OK python deps (skip pip on update)" "Green"
    } else {
        if (-not (Invoke-StudioPipInstall $Root)) { return $false }
    }
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-StudioLog "WARN: web/out missing - git pull should restore it" "Yellow"
    }
    return $true
}

function Invoke-StudioFullUpdate {
    param([string]$Root)
    Write-StudioLog "=== update ($($script:StudioUpdateCoreId)) ===" "Cyan"
    if (-not (Invoke-StudioGit $Root)) { return $false }
    if (Test-StudioPythonOk $Root) {
        Write-StudioLog "OK python deps (skip pip on update)" "Green"
    } else {
        if (-not (Invoke-StudioPipInstall $Root)) { return $false }
    }
    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-StudioLog "WARN: web/out missing - git pull should restore it" "Yellow"
    }
    Stop-StudioBackend $Root
    return (Start-StudioBackendWindow $Root)
}
