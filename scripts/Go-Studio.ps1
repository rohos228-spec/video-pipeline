# GO Studio - ASCII only (no smart quotes)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "FAIL: no .venv" -ForegroundColor Red
    exit 1
}

Write-Host "`n==> preflight" -ForegroundColor Cyan
& .venv\Scripts\python.exe -c "from app.web.api import create_app; create_app(); print('create_app OK')"
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n==> stop :8765" -ForegroundColor Cyan
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

Write-Host "`n==> npm run build" -ForegroundColor Cyan
Push-Location web
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; exit 1 }
Pop-Location

if (-not (Test-Path "web\out\index.html")) {
    Write-Host "FAIL: web\out\index.html missing" -ForegroundColor Red
    exit 1
}

Write-Host "`n==> start backend (keep BACKEND window open)" -ForegroundColor Cyan
$cmd = 'cd /d "' + $repo + '" && title BACKEND :8765 && set TELEGRAM_ENABLED=false && set WEB_HOST=127.0.0.1 && set WEB_PORT=8765 && .venv\Scripts\python.exe -m app.main'
Start-Process cmd -ArgumentList '/k', $cmd

Write-Host "`n==> wait health" -ForegroundColor Cyan
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 3
        if ($h.status -eq 'ok') {
            Start-Process 'http://127.0.0.1:8765'
            Write-Host "OK http://127.0.0.1:8765" -ForegroundColor Green
            exit 0
        }
    } catch { }
}

Write-Host "FAIL: backend not up - see BACKEND window" -ForegroundColor Red
exit 1
