@echo off
REM One-click update + Studio (console, PS 5.1 safe)
cd /d "%~dp0"
echo.
echo === UPDATE-STUDIO ===
echo Folder: %CD%
if not exist "%~dp0pyproject.toml" (
    echo.
    echo [ERROR] pyproject.toml NOT in this folder.
    echo Copy/move the whole video-pipeline repo here, or run this .cmd from repo root.
    echo NOT from C:\Users\...\Love Space — need the project folder.
    echo.
    pause
    exit /b 1
)
echo OK: pyproject.toml found
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
