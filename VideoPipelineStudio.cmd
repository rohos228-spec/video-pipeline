@echo off
REM Video Pipeline Studio — запуск GUI (двойной клик)
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File "%~dp0installer\VideoPipelineLauncher.ps1"
if errorlevel 1 pause
