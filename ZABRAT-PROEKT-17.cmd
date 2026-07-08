@echo off
REM MAIN PC (hub): zabrat proekt s NucBox bez UI. Dvoinoj klik.
REM   ZABRAT-PROEKT-17.cmd
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Zabrat-S-Nucbox.ps1" -ProjectId 17
pause
