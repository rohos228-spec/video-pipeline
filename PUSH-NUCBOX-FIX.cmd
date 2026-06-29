@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\push-fleet-fix-to-nucbox.ps1"
if errorlevel 1 pause
exit /b %ERRORLEVEL%
