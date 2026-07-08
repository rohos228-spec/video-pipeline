@echo off
cd /d "%~dp0"
title Video Pipeline Studio
if not exist pyproject.toml (
    echo ERROR: zapuskaj iz papki video-pipeline
    pause
    exit /b 1
)
echo Zapusk Studio (apply-local, bez git)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply-local.ps1" -SkipBuild
pause
