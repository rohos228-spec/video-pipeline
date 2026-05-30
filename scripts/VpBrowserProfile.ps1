# Chrome profile for video-pipeline (ChatGPT, outsee). Logins: %USERPROFILE%\.vp_browser_data

function Get-VpRepoRoot {
    param([string]$StartDir = $PSScriptRoot)
    $dir = $StartDir
    while ($dir) {
        if (Test-Path (Join-Path $dir "pyproject.toml")) { return $dir }
        $parent = Split-Path $dir -Parent
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    return (Get-Location).Path
}

function Get-VpBrowserUserDataDir {
    $default = Join-Path $env:USERPROFILE ".vp_browser_data"

    if ($env:BROWSER_USER_DATA_DIR) {
        $raw = $env:BROWSER_USER_DATA_DIR.Trim().Trim('"').Trim("'")
        if ($raw -match '^~[/\\]') {
            $raw = Join-Path $env:USERPROFILE $raw.Substring(2)
        }
        if (Test-Path $raw) { return $raw }
        Write-Warning "BROWSER_USER_DATA_DIR env var not found: $raw"
        Write-Warning "Using default: $default"
        return $default
    }

    $repo = Get-VpRepoRoot
    $envFile = Join-Path $repo ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding UTF8) {
            if ($line -match '^\s*#\s*BROWSER_USER_DATA_DIR') { continue }
            if ($line -match '^\s*BROWSER_USER_DATA_DIR\s*=\s*(.+)\s*$') {
                $raw = $Matches[1].Trim().Trim('"').Trim("'")
                if (-not $raw) { break }
                if ($raw -match '^~[/\\]') {
                    $raw = Join-Path $env:USERPROFILE $raw.Substring(2)
                }
                if (Test-Path $raw) { return $raw }
                Write-Warning ".env BROWSER_USER_DATA_DIR not found: $raw"
                Write-Warning "Using default: $default"
                return $default
            }
        }
    }
    return $default
}

function Find-VpChromeExe {
    $chromePaths = @(
        "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    return $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Test-VpChromeCdp {
    param([int]$Port = 29229)
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Clear-VpChromeProfileLocks {
    param([string]$UserDataDir)
    $lockNames = @("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile")
    foreach ($name in $lockNames) {
        $path = Join-Path $UserDataDir $name
        if (Test-Path $path) {
            try {
                Remove-Item -LiteralPath $path -Force -ErrorAction Stop
                Write-Host "Removed stale lock: $name" -ForegroundColor DarkGray
            } catch {
                Write-Warning "Could not remove lock $name - close Chrome and retry"
            }
        }
    }
}

function Stop-VpChromeProcesses {
    $procs = @(Get-Process -Name chrome -ErrorAction SilentlyContinue)
    if (-not $procs) { return 0 }
    Write-Host "Stopping $($procs.Count) chrome.exe process(es)..." -ForegroundColor DarkYellow
    Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    return $procs.Count
}

function Ensure-VpChromeClosedForCdp {
    param([int]$Port = 29229)
    if (Test-VpChromeCdp -Port $Port) { return }

    $procs = @(Get-Process -Name chrome -ErrorAction SilentlyContinue)
    if (-not $procs) { return }

    Write-Host ""
    Write-Host "Chrome is running but CDP :$Port is OFF." -ForegroundColor Yellow
    Write-Host "Pipeline needs a dedicated Chrome with --remote-debugging-port=$Port." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Close ALL Chrome windows, then press Enter (or type kill to force-stop)..." -ForegroundColor Cyan
    $answer = Read-Host
    if ($answer -match '^(kill|y|yes)$') {
        Stop-VpChromeProcesses | Out-Null
        return
    }

    $still = @(Get-Process -Name chrome -ErrorAction SilentlyContinue)
    if ($still) {
        Write-Host "Chrome still running - force stopping..." -ForegroundColor DarkYellow
        Stop-VpChromeProcesses | Out-Null
    }
}

function Show-VpChromeDiagnostics {
    param([int]$Port = 29229)
    $chrome = Find-VpChromeExe
    $profile = Get-VpBrowserUserDataDir
    $cdp = Test-VpChromeCdp -Port $Port
    $procs = @(Get-Process -Name chrome -ErrorAction SilentlyContinue)

    Write-Host ""
    Write-Host "=== Chrome diagnostics ===" -ForegroundColor Cyan
    Write-Host "Chrome exe:  $(if ($chrome) { $chrome } else { 'NOT FOUND' })"
    Write-Host "Profile dir: $profile"
    Write-Host "Profile ok:  $(Test-Path $profile)"
    Write-Host "CDP :$Port : $(if ($cdp) { 'OK' } else { 'not responding' })"
    Write-Host "chrome.exe:  $($procs.Count) process(es)"
    foreach ($name in @("SingletonLock", "lockfile")) {
        $p = Join-Path $profile $name
        if (Test-Path $p) { Write-Host "Lock file:   $name (may block start)" -ForegroundColor Yellow }
    }
    Write-Host ""
}

function Start-VpChromeCdp {
    param(
        [int]$Port = 29229,
        [string]$OpenUrl = "",
        [switch]$ForceNew,
        [switch]$SkipCloseCheck
    )
    $chrome = Find-VpChromeExe
    if (-not $chrome) {
        throw "Chrome not found. Install Google Chrome from https://www.google.com/chrome/"
    }

    $userDataDir = Get-VpBrowserUserDataDir
    if (-not (Test-Path $userDataDir)) {
        New-Item -ItemType Directory -Path $userDataDir -Force | Out-Null
    }

    if ((Test-VpChromeCdp -Port $Port) -and -not $ForceNew) {
        Write-Host "[ok] Chrome CDP already on :$Port profile: $userDataDir" -ForegroundColor Green
        if ($OpenUrl) {
            Start-Process -FilePath $chrome -ArgumentList @("--user-data-dir=$userDataDir", $OpenUrl)
        }
        return
    }

    if (-not $SkipCloseCheck) {
        Ensure-VpChromeClosedForCdp -Port $Port
    }

    Clear-VpChromeProfileLocks -UserDataDir $userDataDir

    Write-Host "Starting Chrome CDP :$Port" -ForegroundColor Cyan
    Write-Host "Chrome:  $chrome" -ForegroundColor DarkGray
    Write-Host "Profile: $userDataDir" -ForegroundColor DarkGray

    $args = @(
        "--remote-debugging-port=$Port",
        "--user-data-dir=$userDataDir",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking"
    )
    if ($OpenUrl) { $args += $OpenUrl }

    try {
        $proc = Start-Process -FilePath $chrome -ArgumentList $args -PassThru
        Write-Host "Started chrome.exe PID $($proc.Id)" -ForegroundColor DarkGray
    } catch {
        throw "Start-Process failed: $($_.Exception.Message)"
    }

    $maxWait = 45
    $waited = 0
    while ($waited -lt $maxWait) {
        Start-Sleep -Seconds 1
        $waited++
        if (Test-VpChromeCdp -Port $Port) {
            Write-Host "[ok] CDP ready in $waited sec" -ForegroundColor Green
            return
        }
        $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if (-not $alive -and $waited -gt 3) {
            throw "Chrome exited immediately (PID $($proc.Id)). Run Diagnose-Chrome.cmd or check profile locks."
        }
    }

    Write-Warning "CDP did not respond in $maxWait sec."
    Show-VpChromeDiagnostics -Port $Port
    Write-Host "Try: close all Chrome, run Diagnose-Chrome.cmd, then Start-Chrome.cmd again." -ForegroundColor Yellow
}
