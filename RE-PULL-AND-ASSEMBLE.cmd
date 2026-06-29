@echo off

cd /d "%~dp0"

echo === Re-pull project #17 from NucBox to hub (slug zhutkie...) ===

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Zabrat-S-Nucbox.ps1" -ProjectId 17

echo.

echo === repair + assemble hub project #15 ===

set PY=%~dp0.venv\Scripts\python.exe

if not exist "%PY%" set PY=python

"%PY%" scripts\assemble_r15_direct.py 15

pause

