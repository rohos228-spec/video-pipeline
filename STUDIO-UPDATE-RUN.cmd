@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0UPDATE.ps1" -SkipPull -Run
pause
