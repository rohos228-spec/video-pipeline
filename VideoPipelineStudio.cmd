@echo off
REM Video Pipeline Studio - launch GUI from repo root
cd /d "%~dp0"

set "LAUNCHER=%~dp0installer\VideoPipelineLauncher.ps1"
findstr /C:"launcher-update-verify-ui-v81" "%LAUNCHER%" >nul 2>&1
if errorlevel 1 (
    findstr /C:"launcher-smart-update-v80" "%LAUNCHER%" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [ERROR] Old launcher. In repo folder run:
        echo   git pull origin cursor/fix-launcher-update-start-977b
        echo   force-rebuild-web.cmd
        echo Then reopen VideoPipelineStudio.cmd
        echo.
        pause
        exit /b 1
    )
)

powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File "%LAUNCHER%"
if errorlevel 1 pause
