@echo off
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ERROR: not in video-pipeline folder
    pause
    exit /b 1
)
echo Starting backend (keep that window open)...
start "video-pipeline backend" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-backend.ps1"
echo Waiting for http://127.0.0.1:8765 (up to 120 sec)...
powershell -NoProfile -Command "$d=(Get-Date).AddSeconds(120); while((Get-Date)-lt $d){ try { if((Invoke-WebRequest 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200){ Write-Host '[OK] Backend ready'; exit 0 } } catch {}; Start-Sleep -Milliseconds 500 }; Write-Host '[FAIL] Backend not up - read the backend window (Preflight or traceback)'; exit 1"
if errorlevel 1 (
    echo.
    echo Fix: git pull, then START-STUDIO.cmd again. Or run check-backend.cmd
    pause
    exit /b 1
)
start http://127.0.0.1:8765
echo.
echo Version: powershell -Command "Invoke-RestMethod http://127.0.0.1:8765/api/studio-version"
pause
