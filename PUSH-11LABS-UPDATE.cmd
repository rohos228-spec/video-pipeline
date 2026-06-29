@echo off
setlocal
cd /d "%~dp0"
echo Pushing full Studio update to GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\push-11labs-update.ps1"
if errorlevel 1 (
  echo.
  echo PUSH FAILED - see data\push-11labs.log
  pause
  exit /b 1
)
echo.
echo PUSH OK - see data\push-11labs.log
pause
