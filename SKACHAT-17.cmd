@echo off
chcp 65001 >nul
title GLAVNY PC - skachat proekt 17 s NucBox
cd /d "%~dp0"
echo.
echo GLAVNY PC: skachat proekt #17 s NucBox
echo Backend dolzhen byt zapuschen (run-backend.ps1)
echo NucBox tozhe dolzhen byt online
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Zabrat-S-Nucbox.ps1" -ProjectId 17
echo.
pause
