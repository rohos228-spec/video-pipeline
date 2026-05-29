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
    if ($env:BROWSER_USER_DATA_DIR) {
        return $env:BROWSER_USER_DATA_DIR
    }
    $repo = Get-VpRepoRoot
    $envFile = Join-Path $repo ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding UTF8) {
            if ($line -match '^\s*BROWSER_USER_DATA_DIR\s*=\s*(.+)\s*$') {
                $raw = $Matches[1].Trim().Trim('"').Trim("'")
                if ($raw) {
                    if ($raw -match '^~[/\\]') {
                        $raw = Join-Path $env:USERPROFILE $raw.Substring(2)
                    }
                    return $raw
                }
            }
        }
    }
    return Join-Path $env:USERPROFILE ".vp_browser_data"
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

function Start-VpChromeCdp {
    param(
        [int]$Port = 29229,
        [string]$OpenUrl = "",
        [switch]$ForceNew
    )
    $chrome = Find-VpChromeExe
    if (-not $chrome) {
        throw "Chrome not found. Install Google Chrome."
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

    Write-Host "Starting Chrome CDP :$Port" -ForegroundColor Cyan
    Write-Host "Profile: $userDataDir" -ForegroundColor DarkGray
    $args = @(
        "--remote-debugging-port=$Port",
        "--user-data-dir=$userDataDir",
        "--no-first-run",
        "--no-default-browser-check"
    )
    if ($OpenUrl) { $args += $OpenUrl }
    Start-Process -FilePath $chrome -ArgumentList $args

    $waited = 0
    while ($waited -lt 25) {
        Start-Sleep -Seconds 1
        $waited++
        if (Test-VpChromeCdp -Port $Port) {
            Write-Host "[ok] CDP ready in $waited sec" -ForegroundColor Green
            return
        }
    }
    Write-Warning "CDP did not respond in 25 sec - check Chrome window"
}
