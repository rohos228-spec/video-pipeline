@echo off
REM Start backend — keep this window open
cd /d "%~dp0"
findstr /C:"RUN_BACKEND_ID=session-log-v2" "%~dp0run-backend.ps1" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] OLD run-backend.ps1 - backend.log will lock and crash.
    echo   Double-click: fix-update-files.cmd
    echo   Or in Launcher: * Update + Start
    echo.
    pause
    exit /b 1
)
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0run-backend.ps1"
