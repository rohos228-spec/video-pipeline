@echo off
REM Обновить run-backend + launcher без полного merge (если backend.log lock / старый скрипт)
cd /d "%~dp0"
echo === fix-update-files ===
echo Folder: %CD%
echo.

echo [1] stop old backend...
call "%~dp0scripts\stop-backend.ps1" -Quiet 2>nul
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0scripts\stop-backend.ps1" -Quiet

echo.
echo [2] git fetch...
git fetch origin cursor/fix-launcher-update-start-977b
git fetch origin devin/windows-installer

echo.
echo [3] checkout critical files...
git checkout origin/cursor/fix-launcher-update-start-977b -- run-backend.ps1 scripts/stop-backend.ps1 stop-backend.cmd installer/VideoPipelineLauncher.ps1 2>nul
if errorlevel 1 (
    git checkout origin/devin/windows-installer -- run-backend.ps1 scripts/stop-backend.ps1 stop-backend.cmd installer/VideoPipelineLauncher.ps1
)

findstr /C:"RUN_BACKEND_ID=session-log-v2" run-backend.ps1 >nul
if errorlevel 1 (
    echo [FAIL] run-backend.ps1 still OLD
    pause
    exit /b 1
)
echo [OK] run-backend.ps1 session-log-v2

echo.
echo Done. Close ALL backend windows, then:
echo   VideoPipelineStudio.cmd  -^>  * Update + Start
echo.
pause
