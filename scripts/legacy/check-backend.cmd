@echo off
cd /d "%~dp0"
echo === Backend check ===
echo Folder: %CD%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [FAIL] .venv missing - Launcher button 1 Full install
    goto end
)
echo [OK] .venv

if not exist "web\out\index.html" (
    echo [WARN] web\out\index.html missing - button 6 Build Web UI
) else (
    echo [OK] web UI built
)

findstr /C:"Invoke-ButtonAction" "installer\VideoPipelineLauncher.ps1" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Old launcher - git pull or update-launcher.cmd
) else (
    echo [OK] Launcher buttons fixed
)

echo.
echo Git:
git rev-parse --short HEAD 2>nul
echo.

powershell -NoProfile -Command "try { $r = Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 3; Write-Host '[OK] Backend on :8765' $r.status; try { $sv = Invoke-RestMethod 'http://127.0.0.1:8765/api/studio-version' -TimeoutSec 3; Write-Host ('       version: ' + $sv.label) } catch {} } catch { Write-Host '[FAIL] Backend NOT running on :8765'; Write-Host '       Start: .\start-backend.cmd  (keep window open)'; Write-Host '       Or: .\START-STUDIO.cmd'; Write-Host '       Log: data\backend-*.log'; $py = Join-Path (Get-Location) '.venv\Scripts\python.exe'; if (Test-Path $py) { Write-Host ''; Write-Host 'Preflight (why startup may fail):'; & $py -c 'from app.web.api import create_app; create_app(); print(\"  create_app OK\")' 2>&1 | ForEach-Object { Write-Host $_ } } }"

echo.
netstat -ano | findstr ":8765" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
    echo Port 8765: not listening
) else (
    echo Port 8765: listening
)

:end
echo.
pause
