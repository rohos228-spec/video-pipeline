@echo off
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ERROR: run from video-pipeline folder
    pause
    exit /b 1
)
echo.
echo Do NOT press Y to cancel - wait for OK or FAIL at the end.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Continue-Install.ps1"
echo.
if errorlevel 1 (
    echo FAILED - run this cmd again when internet is stable
) else (
    echo SUCCESS - now run UPDATE-STUDIO.cmd
)
echo.
pause
