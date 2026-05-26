@echo off
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ERROR: run from video-pipeline folder
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Update-Studio.ps1"
if errorlevel 1 pause
