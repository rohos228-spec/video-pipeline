@echo off
REM Video Pipeline Studio - launch GUI from repo root
cd /d "%~dp0"

set "LAUNCHER=%~dp0installer\VideoPipelineLauncher.ps1"
findstr /C:"ASCII-only for Windows PowerShell" "%LAUNCHER%" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Old launcher file detected. Update first:
    echo   git pull origin cursor/premium-studio-ui-3713
    echo   git checkout origin/cursor/premium-studio-ui-3713 -- installer/VideoPipelineLauncher.ps1
    echo.
    echo Or run:  update-launcher.cmd
    echo.
    pause
    exit /b 1
)

powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File "%LAUNCHER%"
if errorlevel 1 pause
