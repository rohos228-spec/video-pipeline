@echo off
setlocal
cd /d "%~dp0"
title CHECK CLONE

echo.
echo Тест клона через curl + proxy из .env
echo.

if not exist ".venv\Scripts\python.exe" (
    echo FAIL: no .venv
    pause
    exit /b 1
)

where curl >nul 2>&1
if errorlevel 1 (
    echo WARN: curl.exe not in PATH
) else (
    curl --version | findstr /i "curl"
)

echo.
echo Installing requests[socks] if missing...
.venv\Scripts\pip install "requests[socks]>=2.32" -q
echo.
.venv\Scripts\python.exe scripts\check_clone.py
set ERR=%ERRORLEVEL%
echo.
echo === RESULT ===
type data\check-clone-result.txt
echo.
pause
exit /b %ERR%
