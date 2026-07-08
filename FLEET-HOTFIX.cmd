@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Fleet hotfix
echo.
echo === GLAVNY PC ===
echo Esli ty NA GLAVNOM PC: tolko restart (fajly uzhe dolzhny byt v papke):
echo   powershell -ep bypass -File apply-local.ps1 -SkipBuild -NoBrowser
echo.
echo === NUCBOX ===
echo Esli ty NA NUCBOX: snachala zalij kod na glavny:
echo   ZALIT-HOTFIX-NA-HUB.cmd
echo.
set /p WHERE="Ty na NucBox (N) ili Glavny PC (G)? [N/G]: "
if /I "%WHERE%"=="G" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply-local.ps1" -SkipBuild -NoBrowser
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Zalit-Hotfix-Na-Hub.ps1"
)
pause
