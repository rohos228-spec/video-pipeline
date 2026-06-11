@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\push-v160.ps1"
if errorlevel 1 (
  echo.
  echo PUSH-V160 FAILED - see errors above.
  pause
  exit /b 1
)
echo.
echo PUSH-V160 OK
pause
