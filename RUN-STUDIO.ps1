# One window. UI: http://127.0.0.1:8765
# powershell -ExecutionPolicy Bypass -File "C:\Users\Love Space\video-pipeline\RUN-STUDIO.ps1"

param(
    [switch]$SkipChrome,
    [switch]$NoBrowser
)

function Start-VideoPipelineStudio {
    param(
        [string]$Root,
        [switch]$SkipChrome,
        [switch]$NoBrowser
    )

    $ErrorActionPreference = "Stop"
    if (-not $Root) { $Root = Split-Path -Parent $PSCommandPath }
    Set-Location -LiteralPath $Root

    if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
        Write-Host "ERROR: not in video-pipeline folder" -ForegroundColor Red
        exit 1
    }

    if (-not (Test-Path (Join-Path $Root "web\out\index.html"))) {
        Write-Host "ERROR: web/out missing - run UPDATE.ps1 first" -ForegroundColor Red
        exit 1
    }

    $stop = Join-Path $Root "scripts\stop-backend.ps1"
    if (Test-Path $stop) {
        Write-Host "==> stop :8765" -ForegroundColor Cyan
        & $stop -Quiet
        Start-Sleep -Seconds 1
    }

    if (-not $SkipChrome) {
        $chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
        $cdpOk = $false
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:29229/json/version" -TimeoutSec 2 -UseBasicParsing
            $cdpOk = ($r.StatusCode -eq 200)
        } catch { $cdpOk = $false }
        if (-not $cdpOk -and (Test-Path $chrome)) {
            Write-Host "==> Chrome CDP :29229" -ForegroundColor Yellow
            Start-Process -FilePath $chrome -ArgumentList @(
                "--remote-debugging-port=29229",
                "--user-data-dir=$env:USERPROFILE\.vp_browser_data"
            )
            Start-Sleep -Seconds 2
        }
    }

    $waitJob = $null
    if (-not $NoBrowser) {
        $waitJob = Start-Job -ScriptBlock {
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
    }

    Write-Host "==> backend (Ctrl+C = stop). Wait: Uvicorn running on :8765" -ForegroundColor Cyan
    & (Join-Path $Root "run-backend.ps1") -NoPause
    if ($waitJob) {
        Stop-Job $waitJob -ErrorAction SilentlyContinue
        Remove-Job $waitJob -Force -ErrorAction SilentlyContinue
    }
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-VideoPipelineStudio -Root $Root -SkipChrome:$SkipChrome -NoBrowser:$NoBrowser
