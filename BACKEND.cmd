@echo off
cd /d "%~dp0"
title video-pipeline BACKEND
if not exist .venv\Scripts\python.exe (
    echo Run install.ps1 first
    pause
    exit /b 1
)
set TELEGRAM_ENABLED=false
echo.
echo DO NOT CLOSE - wait for: Uvicorn running on http://127.0.0.1:8765
echo.
.venv\Scripts\python.exe -m app.main
echo.
echo Backend stopped. Code %ERRORLEVEL%
pause
