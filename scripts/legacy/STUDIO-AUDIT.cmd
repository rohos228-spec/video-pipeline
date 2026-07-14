@echo off
REM Studio Guardian: API smoke (+ optional Playwright with -E2E)
REM Double-click or: STUDIO-AUDIT.cmd
REM With e2e: STUDIO-AUDIT.cmd -E2E
cd /d "%~dp0"
if not exist "scripts\guardian\run-studio-audit.ps1" (
    echo [FAIL] scripts\guardian\run-studio-audit.ps1 not found
    echo Run from video-pipeline folder, not from C:\Users\Love Space
    pause
    exit /b 1
)
if /i "%~1"=="-E2E" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\guardian\run-studio-audit.ps1" -E2E
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\guardian\run-studio-audit.ps1"
)
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" pause
exit /b %EC%
