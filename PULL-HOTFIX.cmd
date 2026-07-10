@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0scripts\Pull-Hotfix-Safe.ps1" %*
if errorlevel 1 pause
exit /b %errorlevel%
