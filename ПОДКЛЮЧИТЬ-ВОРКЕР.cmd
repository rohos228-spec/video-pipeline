@echo off
cd /d "%~dp0"
title Fleet worker setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Setup-FleetWorker.ps1"
pause
