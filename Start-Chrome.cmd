@echo off
cd /d "%~dp0"
title Chrome CDP (video-pipeline)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-ChromeCDP.ps1"
pause
