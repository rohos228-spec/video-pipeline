@echo off
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ERROR: not in video-pipeline folder
    pause
    exit /b 1
)
echo Starting backend...
start "video-pipeline backend" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-backend.ps1"
echo Waiting 15 sec...
timeout /t 15 /nobreak >nul
start http://127.0.0.1:8765
echo.
echo If page does not load: read the backend window for errors.
echo Version check: http://127.0.0.1:8765/api/studio-version
pause
