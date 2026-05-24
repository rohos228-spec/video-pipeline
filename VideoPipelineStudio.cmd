@echo off
REM Video Pipeline Studio — запуск GUI (двойной клик из корня video-pipeline)
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Normal -File "%~dp0installer\VideoPipelineLauncher.ps1"
if errorlevel 1 pause
