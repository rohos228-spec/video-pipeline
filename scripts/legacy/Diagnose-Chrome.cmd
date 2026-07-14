@echo off
cd /d "%~dp0"
title Chrome diagnostics
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Diagnose-Chrome.ps1"
pause
