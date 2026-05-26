# Finish pip install on slow/unstable internet. Do NOT press Y to cancel.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ERROR: run install.ps1 first" -ForegroundColor Red
    exit 1
}
$spec = (Resolve-Path -LiteralPath $Root).Path
$env:PIP_DEFAULT_TIMEOUT = "600"
$pipArgs = @(
    "-m", "pip", "install",
    "--default-timeout=600",
    "--retries", "15",
    "--progress-bar", "on"
)

Write-Host ""
Write-Host "=== CONTINUE-INSTALL (do not cancel with Y) ===" -ForegroundColor Cyan
Write-Host "Repo: $spec" -ForegroundColor Gray
Write-Host "If it fails: better Wi-Fi, disable VPN, run again." -ForegroundColor Gray
Write-Host ""

function Invoke-PipStep([string]$Label, [string[]]$ExtraArgs) {
    for ($i = 1; $i -le 8; $i++) {
        Write-Host ">> $Label (try $i/8)..." -ForegroundColor Yellow
        & $py @pipArgs @ExtraArgs
        if ($LASTEXITCODE -eq 0) {
            Write-Host "   OK $Label" -ForegroundColor Green
            return $true
        }
        Write-Host "   failed, wait 30 sec..." -ForegroundColor DarkYellow
        Start-Sleep -Seconds 30
    }
    return $false
}

# 1) Main app (no faster-whisper - small download)
if (-not (Invoke-PipStep "pip install project" @("-e", $spec))) {
    Write-Host "FAIL: could not install project" -ForegroundColor Red
    exit 1
}

# 2) Quick check
& $py -c "import fastapi, sqlalchemy, playwright"
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: core imports broken" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "OK Studio can run. Optional whisper (big download):" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\python.exe -m pip install -e `"${spec}[whisper]`"" -ForegroundColor Gray
Write-Host "Next: .\UPDATE-STUDIO.cmd" -ForegroundColor Green
Write-Host ""
exit 0
