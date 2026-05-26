# Video Pipeline — обновить код + запустить Studio
# Двойной клик или: powershell -ExecutionPolicy Bypass -File START.ps1

$Repo = "C:\Users\Love Space\video-pipeline"
$Branch = "cursor/fix-video-rerun-skip-977b"

Set-Location -LiteralPath $Repo
if (-not (Test-Path "pyproject.toml")) {
    Write-Host "ERROR: wrong folder: $Repo" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "=== video-pipeline START ===" -ForegroundColor Cyan
Write-Host "Folder: $Repo"

git fetch origin $Branch
git checkout -B $Branch "origin/$Branch"
git reset --hard "origin/$Branch"
git checkout "origin/$Branch" -- web/out web/STUDIO_VERSION 2>$null

Write-Host "Git:" (git rev-parse --short HEAD)
Get-Content web\STUDIO_VERSION -TotalCount 2

$py = "$Repo\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "No .venv — run install.ps1 once" -ForegroundColor Red
    pause
    exit 1
}

& $py -c "from app.web.api import create_app; create_app(); print('Backend OK')"
if ($LASTEXITCODE -ne 0) { pause; exit 1 }

Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*video-pipeline*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 2

$env:TELEGRAM_ENABLED = "false"
Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-File","$Repo\run-backend.ps1" -WorkingDirectory $Repo

Write-Host "Wait for: Uvicorn running on http://127.0.0.1:8765" -ForegroundColor Yellow
Start-Sleep 12
Start-Process "http://127.0.0.1:8765"
Write-Host "Browser Ctrl+F5 if old version"
pause
