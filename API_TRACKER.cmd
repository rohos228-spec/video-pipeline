@echo off
title API Tracker & Cost Monitor
chcp 65001 >nul
cd /d "%~dp0api-tracker"

if exist "..\.venv\Scripts\pythonw.exe" (
    start "" "..\.venv\Scripts\pythonw.exe" main.py
) else if exist "..\.venv\Scripts\python.exe" (
    start "" "..\.venv\Scripts\python.exe" main.py
) else if exist "dist\API-Tracker\API-Tracker.exe" (
    start "" "dist\API-Tracker\API-Tracker.exe"
) else (
    start "" python main.py
)
exit /b 0
