@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\download-torch-cuda.ps1"
if errorlevel 1 pause
if not errorlevel 1 pause
