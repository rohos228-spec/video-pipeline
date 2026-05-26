@echo off
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ERROR: run from video-pipeline folder
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Continue-Install.ps1"
pause
exit /b %ERRORLEVEL%
