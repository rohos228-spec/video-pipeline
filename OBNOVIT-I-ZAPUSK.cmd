@echo off
cd /d "%~dp0"
title Obnovit i zapusk bota
if not exist pyproject.toml (
    echo ERROR: zapuskaj iz papki video-pipeline
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Obnovit-i-Zapusk.ps1" %*
exit /b %ERRORLEVEL%
