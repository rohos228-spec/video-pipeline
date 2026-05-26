# Shared Studio update logic — PowerShell 5.1, ASCII only
# Dot-source: . "$PSScriptRoot\scripts\StudioUpdateCore.ps1"

$script:StudioUpdateBranch = 'cursor/fix-launcher-update-start-977b'
$script:StudioUpdateCoreId = 'studio-update-core-v2'

function Get-StudioRepoRoot {
    param([string]$StartDir = (Get-Location).Path)
    $dir = $StartDir
    for ($i = 0; $i -lt 12; $i++) {
        if (Test-Path (Join-Path $dir 'pyproject.toml')) {
            return $dir
        }
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    return $null
}

function Write-StudioLog {
    param(
        [string]$Message,
        [string]$Color = 'Gray'
    )
    $line = "$(Get-Date -Format 'HH:mm:ss')  $Message"
    Write-Host $line -ForegroundColor $Color
}

function Invoke-StudioGit {
    param([string]$Root)
    Write-StudioLog "> git sync -> origin/$($script:StudioUpdateBranch)" 'Cyan'
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-StudioLog 'FAIL: git not in PATH' 'Red'
        return $false
    }
    $br = $script:StudioUpdateBranch
    $before = (git -C $Root rev-parse --short HEAD 2>$null)
    if (-not $before) { $before = '?' }

    $dirty = git -C $Root status --porcelain 2>$null
    if ($dirty) {
        Write-StudioLog 'Local changes -> git stash push -u' 'Yellow'
        git -C $Root stash push -u -m 'studio-update-auto' 2>&1 | ForEach-Object { Write-StudioLog $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-StudioLog 'FAIL: git stash' 'Red'
            return $false
        }
    }

    git -C $Root fetch origin $br 2>&1 | ForEach-Object { Write-StudioLog $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioLog 'FAIL: git fetch (check internet)' 'Red'
        return $false
    }

    git -C $Root checkout -B $br "origin/$br" 2>&1 | ForEach-Object { Write-StudioLog $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-StudioLog 'FAIL: git checkout' 'Red'
        return $false
    }

    $localH = (git -C $Root rev-parse HEAD 2>$null)
    $remoteH = (git -C $Root rev-parse "origin/$br" 2>$null)
    if ($localH -and $remoteH -and ($localH.Trim() -ne $remoteH.Trim())) {
        git -C $Root reset --hard "origin/$br" 2>&1 | ForEach-Object { Write-StudioLog $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-StudioLog 'FAIL: git reset --hard' 'Red'
            return $false
        }
    }

    $after = (git -C $Root rev-parse --short HEAD 2>$null)
    Write-StudioLog "OK git $before -> $after | STUDIO_VERSION=$(Get-StudioVersionLabel $Root)" 'Green'
    return $true
}

function Get-StudioVersionLabel {
    param([string]$Root)
    $vf = Join-Path $Root 'web\STUDIO_VERSION'
    if (-not (Test-Path $vf)) { return '?' }
    $lines = Get-Content $vf -ErrorAction SilentlyContinue
    if (-not $lines) { return '?' }
    $build = $lines[0].Trim()
    $sha = 'dev'
    if ($lines.Count -gt 1 -and $lines[1].Trim()) {
        $raw = $lines[1].Trim()
        if ($raw.Length -gt 7) { $sha = $raw.Substring(0, 7) } else { $sha = $raw }
    }
    if ($sha -eq 'dev') { return "v$build" }
    return "v$build $sha"
}

function Get-StudioVersionBuildNumber {
    param([string]$Root)
    $vf = Join-Path $Root 'web\STUDIO_VERSION'
    if (-not (Test-Path $vf)) { return 0 }
    $line = (Get-Content $vf -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($line -match '^\s*(\d+)') { return [int]$Matches[1] }
    return 0
}

function Get-StudioBuiltVersionBuildNumber {
    param([string]$Root)
    $idx = Join-Path $Root 'web\out\index.html'
    if (-not (Test-Path $idx)) { return 0 }
    try {
        $text = Get-Content $idx -Raw -ErrorAction Stop
    } catch {
        return 0
    }
    if ($text -match 'v(\d+)') { return [int]$Matches[1] }
    return 0
}

function Test-StudioUiReady {
    param([string]$Root)
    $fileBuild = Get-StudioVersionBuildNumber $Root
    $outBuild = Get-StudioBuiltVersionBuildNumber $Root
    if ($fileBuild -le 0) { return $false }
    if ($outBuild -le 0) { return $false }
    if ($fileBuild -ne $outBuild) {
        Write-StudioLog "UI mismatch: STUDIO_VERSION=v$fileBuild web/out=v$outBuild" 'Red'
        return $false
    }
    return $true
}

function Get-StudioNpmCmd {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    if ($machine -or $user) { $env:Path = "$machine;$user" }
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npm) { return $npm.Source }
    $guess = Join-Path ${env:ProgramFiles} 'nodejs\npm.cmd'
    if (Test-Path $guess) { return $guess }
    return $null
}

function Invoke-StudioNpmBuild {
    param([string]$Root)
    $npm = Get-StudioNpmCmd
    if (-not $npm) {
        Write-StudioLog 'FAIL: npm.cmd not found (install Node.js LTS)' 'Red'
        return $false
    }
    $webDir = Join-Path $Root 'web'
    $outDir = Join-Path $webDir 'out'
    if (Test-Path $outDir) {
        Write-StudioLog "Removing $outDir" 'Gray'
        Remove-Item $outDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-StudioLog "> npm install (web)" 'Cyan'
    $p1 = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$npm`" install" `
        -WorkingDirectory $webDir -Wait -PassThru -NoNewWindow
    if ($p1.ExitCode -ne 0) {
        Write-StudioLog "FAIL: npm install exit $($p1.ExitCode)" 'Red'
        return $false
    }
    Write-StudioLog "> npm run build (web/out)" 'Cyan'
    $p2 = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$npm`" run build" `
        -WorkingDirectory $webDir -Wait -PassThru -NoNewWindow
    if ($p2.ExitCode -ne 0) {
        Write-StudioLog "FAIL: npm run build exit $($p2.ExitCode)" 'Red'
        return $false
    }
    if (-not (Test-Path (Join-Path $outDir 'index.html'))) {
        Write-StudioLog 'FAIL: web/out/index.html missing' 'Red'
        return $false
    }
    return (Test-StudioUiReady $Root)
}

function Invoke-StudioPipInstall {
    param([string]$Root)
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) {
        Write-StudioLog 'FAIL: .venv missing — run install.ps1 first' 'Red'
        return $false
    }
    Write-StudioLog '> pip install -e .[dev]  (PS5: single-quoted extras)' 'Cyan'
    & $py -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) {
        Write-StudioLog "FAIL: pip exit $LASTEXITCODE" 'Red'
        return $false
    }
    Write-StudioLog 'OK pip' 'Green'
    return $true
}

function Stop-StudioBackend {
    param([string]$Root)
    Write-StudioLog 'Stopping backend on :8765...' 'Gray'
    $stopScript = Join-Path $Root 'scripts\stop-backend.ps1'
    if (Test-Path $stopScript) {
        & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $stopScript -Quiet 2>&1 |
            ForEach-Object { Write-StudioLog $_ }
    }
    try {
        Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop |
            ForEach-Object {
                if ($_.OwningProcess -gt 0) {
                    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
                }
            }
    } catch { }
    Start-Sleep -Seconds 2
}

function Test-StudioBackendHealth {
    param([int]$TimeoutSec = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-StudioBackendWindow {
    param([string]$Root)
    $rb = Join-Path $Root 'run-backend.ps1'
    if (-not (Test-Path $rb)) {
        Write-StudioLog 'FAIL: run-backend.ps1 missing' 'Red'
        return $false
    }
    Start-Process powershell.exe -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-NoProfile', '-File', $rb
    ) -WorkingDirectory $Root
    Write-StudioLog 'Waiting for http://127.0.0.1:8765 ...' 'Gray'
    if (Test-StudioBackendHealth -TimeoutSec 120) {
        Write-StudioLog 'OK backend ready' 'Green'
        try {
            $sv = Invoke-RestMethod 'http://127.0.0.1:8765/api/studio-version' -TimeoutSec 5
            Write-StudioLog "API version: $($sv.label) ui_baked=v$($sv.ui_baked_build)" 'Green'
        } catch { }
        Start-Process 'http://127.0.0.1:8765'
        return $true
    }
    Write-StudioLog 'FAIL: backend timeout — read data\backend-*.log' 'Red'
    return $false
}

function Invoke-StudioUpdateOnly {
    param([string]$Root)
    Write-StudioLog "=== Studio update ($($script:StudioUpdateCoreId)) ===" 'Cyan'
    Write-StudioLog "Root: $Root" 'Gray'
    if (-not (Invoke-StudioGit $Root)) { return $false }
    if (-not (Invoke-StudioPipInstall $Root)) { return $false }
    if (Test-StudioUiReady $Root) {
        Write-StudioLog "OK prebuilt UI v$(Get-StudioVersionBuildNumber $Root) from git (no npm)" 'Green'
        return $true
    }
    return (Invoke-StudioNpmBuild $Root)
}

function Invoke-StudioFullUpdate {
    param(
        [string]$Root,
        [switch]$SkipStart
    )
    if (-not (Invoke-StudioUpdateOnly $Root)) { return $false }
    if ($SkipStart) {
        Write-StudioLog 'Update done (-SkipStart)' 'Green'
        return $true
    }
    Stop-StudioBackend $Root
    return (Start-StudioBackendWindow $Root)
}
