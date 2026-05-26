@echo off
cd /d "%~dp0"
title Studio update
if not exist pyproject.toml (
    echo.
    echo ERROR: open folder Documents\video-pipeline
    echo.
    pause
    exit /b 1
)
echo.
echo ========================================
echo   STUDIO UPDATE  (UI v109 + backend)
echo   %CD%
echo ========================================
echo.

where git >nul 2>&1
if not errorlevel 1 (
    echo Downloading latest from GitHub...
    git fetch origin cursor/fix-launcher-update-start-977b 2>nul
    git checkout -B cursor/fix-launcher-update-start-977b origin/cursor/fix-launcher-update-start-977b 2>nul
    git reset --hard origin/cursor/fix-launcher-update-start-977b 2>nul
    git checkout origin/cursor/fix-launcher-update-start-977b -- web/out web/STUDIO_VERSION 2>nul
    echo Git: 
    git rev-parse --short HEAD 2>nul
    echo STUDIO_VERSION:
    type web\STUDIO_VERSION 2>nul
    echo.
) else (
    echo WARN: git not found - using files already on disk
    echo.
)

echo Starting backend... Do NOT close backend window.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Update-Studio.ps1"
set ERR=%ERRORLEVEL%
echo.
if %ERR%==0 (
    echo OK. Browser: http://127.0.0.1:8765  press Ctrl+F5
    start http://127.0.0.1:8765/
) else (
    echo FAILED. Read red lines. Open start-backend.cmd manually.
)
echo.
pause
exit /b %ERR%
