@echo off
REM GUI launcher (buttons). Console update: UPDATE-STUDIO.cmd
cd /d "%~dp0"

set "BR=devin/windows-installer"
where git >nul 2>&1
if not errorlevel 1 (
    git fetch origin %BR% 2>nul
    git checkout -B %BR% origin/%BR% 2>nul
    git reset --hard origin/%BR% 2>nul
)

powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File "%~dp0installer\VideoPipelineLauncher.ps1"
if errorlevel 1 (
    echo.
    echo GUI failed? Try: UPDATE-STUDIO.cmd
    pause
)
