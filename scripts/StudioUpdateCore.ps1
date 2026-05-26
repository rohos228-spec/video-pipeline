# PowerShell 5.1 - ASCII only (no em-dash / unicode quotes)
$script:StudioUpdateBranch = "cursor/fix-launcher-update-start-977b"
$script:StudioUpdateCoreId = "studio-update-core-v3"

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

function Invoke-StudioGit {
    param([string]$Root)
    Write-StudioLog "> git pull ($($script:StudioUpdateBranch))" "Cyan"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-StudioLog "FAIL: git not found" "Red"
        return $false
    }
    $br = $script:StudioUpdateBranch
    git -C $Root fetch origin $br 2>&1 | ForEach-Object { Write-StudioLog $_ }
    if ($LASTEXITCODE -ne 0) { return $false }
    git -C $Root checkout -B $br "origin/$br" 2>&1 | ForEach-Object { Write-StudioLog $_ }
    if ($LASTEXITCODE -ne 0) { return $false }
    git -C $Root reset --hard "origin/$br" 2>&1 | ForEach-Object { Write-StudioLog $_ }
    if ($LASTEXITCODE -ne 0) { return $false }
    Write-StudioLog "OK git $(git -C $Root rev-parse --short HEAD)" "Green"
    return $true
}

function Test-StudioPythonOk {
    param([string]$Root)
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) { return $false }
    & $py -c "import fastapi, sqlalchemy" 2>$null
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

function Start-StudioBackendWindow {
    param([string]$Root)
    $rb = Join-Path $Root "run-backend.ps1"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $rb -WorkingDirectory $Root
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:8765/api/health" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) {
                Start-Process "http://127.0.0.1:8765"
                Write-StudioLog "OK http://127.0.0.1:8765" "Green"
                return $true
            }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    Write-StudioLog "FAIL: backend not up - see run-backend window" "Red"
    return $false
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
        Write-StudioLog "WARN: web/out missing - UI from git pull should fix after next pull" "Yellow"
    }
    Stop-StudioBackend $Root
    return (Start-StudioBackendWindow $Root)
}
