@echo off
title API Tracker & Cost Monitor
chcp 65001 >nul
cd /d "%~dp0api-tracker"

if exist "dist\API-Tracker\API-Tracker.exe" (
    start "" "dist\API-Tracker\API-Tracker.exe"
    exit /b 0
)

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" (
    start "API Tracker" "%PY%" "%~dp0api-tracker\main.py"
) else (
    start "API Tracker" python "%~dp0api-tracker\main.py"
)
exit /b 0
