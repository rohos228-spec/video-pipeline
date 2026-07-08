@echo off
chcp 65001 >nul
cd /d "%~dp0"
title NucBox -> zalit hotfix na GLAVNY PC
echo.
echo Zalivaet kod s ETOGO PC na glavny (100.72.202.35) po seti.
echo Git na glavnom PC NE nuzhen.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Zalit-Hotfix-Na-Hub.ps1"
echo.
pause
