@echo off
cd /d "%~dp0"
title Restore Chrome profile
set "FROM=%~1"
if not defined FROM (
    echo.
    echo Copy Chrome logins (ChatGPT/outsee) from another PC.
    echo.
    echo Example (Love Space -^> Ai Creator):
    echo   C:\Users\Love Space\.vp_browser_data
    echo.
    set /p FROM="Path to .vp_browser_data on old PC: "
)
if not defined FROM (
    echo ERROR: no path given.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Restore-Chrome-Profile.ps1" -FromPath "%FROM%"
pause
