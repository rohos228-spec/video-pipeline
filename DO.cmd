@echo off
cd /d "%~dp0"
echo.
echo ===== GLAVNY PC (100.72.202.35) - ODIN RAZ: =====
echo cd /d C:\Users\aicreator\video-pipeline
echo FLEET-HOTFIX.cmd
echo.
echo ===== NUCBOX - sejchas: =====
powershell -ep bypass -File "%~dp0apply-local.ps1" -SkipBuild -NoBrowser
start "PUSH-17" cmd /k "%~dp0OTPRAVIT-17-NA-HUB.cmd"
