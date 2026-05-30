# Update + start backend + wait until :8765 answers
$Repo = "C:\Users\Love Space\video-pipeline"
$Branch = "cursor/fix-video-rerun-skip-977b"

Set-Location -LiteralPath $Repo
if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: $Repo not found" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "=== START ===" -ForegroundColor Cyan
git fetch origin $Branch 2>&1 | Out-Null
git checkout -B $Branch "origin/$Branch" 2>&1 | Out-Null
git reset --hard "origin/$Branch" 2>&1 | Out-Null

$py = "$Repo\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Run install.ps1 first" -ForegroundColor Red
    pause
    exit 1
}

& $py -c "from app.web.api import create_app; create_app()" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Preflight FAILED - fix errors above" -ForegroundColor Red
    pause
    exit 1
}

Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        if ($_.Path -and $_.Path -like "*video-pipeline*") { Stop-Process -Id $_.Id -Force }
    } catch {}
}
Start-Sleep 2

$env:TELEGRAM_ENABLED = "false"
Start-Process powershell -ArgumentList @(
    "-NoExit", "-ExecutionPolicy", "Bypass",
    "-File", "$Repo\run-backend.ps1"
) -WorkingDirectory $Repo

Write-Host "Waiting for backend on :8765 (max 120 sec)..." -ForegroundColor Yellow
$ok = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:8765/api/health" -TimeoutSec 2
        if ($h.status -eq "ok") { $ok = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}

if (-not $ok) {
    Write-Host ""
    Write-Host "BACKEND NOT RUNNING" -ForegroundColor Red
    Write-Host "1) Open the other window 'video-pipeline backend'"
    Write-Host "2) Read red text / traceback there"
    Write-Host "3) Or run BACKEND.cmd in this folder (backend in this window)"
    Write-Host ""
    pause
    exit 1
}

$v = Invoke-RestMethod "http://127.0.0.1:8765/api/studio-version" -TimeoutSec 5
Write-Host "OK version:" $v.label -ForegroundColor Green
Start-Process "http://127.0.0.1:8765"
Write-Host "Browser open. Ctrl+F5"
pause
