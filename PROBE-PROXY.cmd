@echo off
setlocal
cd /d "%~dp0"
title PROBE PROXY

if not exist ".venv\Scripts\python.exe" (
    echo FAIL: no .venv
    pause
    exit /b 1
)

.venv\Scripts\python.exe scripts\probe_proxy_ports.py
set ERR=%ERRORLEVEL%
echo.
echo Result: data\proxy-probe.txt
pause
exit /b %ERR%
