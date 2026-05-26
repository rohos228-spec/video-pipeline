@echo off
REM Video Pipeline Studio — one entry point (launcher self-updates from git before GUI)
cd /d "%~dp0"

set "BR=cursor/fix-launcher-update-start-977b"
where git >nul 2>&1
if not errorlevel 1 (
    git fetch origin %BR% >nul 2>&1
    git checkout -B %BR% origin/%BR% >nul 2>&1
)

powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File "%~dp0installer\VideoPipelineLauncher.ps1"
if errorlevel 1 pause
