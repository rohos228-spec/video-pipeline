@echo off
cd /d "%~dp0"
title Fleet pipeline fix
echo === Fleet: rebuild UI + restart backend ===
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply-local.ps1"
exit /b %ERRORLEVEL%
