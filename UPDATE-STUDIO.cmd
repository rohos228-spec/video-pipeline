@echo off
REM One-click update + Studio (console, PS 5.1 safe)
cd /d "%~dp0"
echo.
echo === UPDATE-STUDIO ===
echo Folder: %CD%
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Update-Studio.ps1"
set ERR=%ERRORLEVEL%
echo.
if %ERR% NEQ 0 (
    echo [FAILED] exit code %ERR% — scroll up for FAIL lines
) else (
    echo [OK] Studio should open in browser — keep backend window open
)
echo.
pause
exit /b %ERR%
