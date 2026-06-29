@echo off
cd /d "%~dp0"
title Fleet Hotfix
if not exist pyproject.toml (
    echo ERROR: run from video-pipeline folder
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\FLEET-HOTFIX.ps1"
if errorlevel 1 pause
exit /b %ERRORLEVEL%
