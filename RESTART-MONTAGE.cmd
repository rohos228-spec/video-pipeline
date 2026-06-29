@echo off
setlocal
cd /d "%~dp0"
set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo === stop backend ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-backend.ps1" -Quiet
timeout /t 2 /nobreak >nul

echo === start backend (new window) ===
start "VideoPipeline-Backend" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-backend.ps1" -NoPause

echo === wait for :8765 ===
set /a n=0
:waitloop
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %ERRORLEVEL%==0 goto ready
set /a n+=1
if %n% GEQ 45 goto fail
goto waitloop

:ready
echo === assemble #15 (Excel R15 only) ===
"%PY%" "%~dp0scripts\assemble_r15_direct.py" 15
echo.
echo DONE. Open http://127.0.0.1:8765
pause
exit /b 0

:fail
echo FAIL: backend not up after 90s
pause
exit /b 1
