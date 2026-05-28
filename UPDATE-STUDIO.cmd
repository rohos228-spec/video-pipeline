@echo off
cd /d "%~dp0"
title Studio update
if not exist pyproject.toml (
    echo.
    echo ERROR: run from YOUR video-pipeline folder (where pyproject.toml is)
    echo.
    pause
    exit /b 1
)
echo.
echo ========================================
echo   STUDIO UPDATE
echo   %CD%
echo ========================================
echo.
echo If update fails or version stuck: double-click FORCE-UPDATE.cmd
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Update-Studio.ps1"
set ERR=%ERRORLEVEL%
echo.
if %ERR%==0 (
    echo OK. Browser: http://127.0.0.1:8765  press Ctrl+F5
    start http://127.0.0.1:8765/
) else (
    echo FAILED. Try FORCE-UPDATE.cmd in this folder.
)
echo.
pause
exit /b %ERR%
