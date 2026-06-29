@echo off

cd /d "%~dp0"

set PY=%~dp0.venv\Scripts\python.exe

if not exist "%PY%" set PY=python

echo === pause + assemble #15 ===

"%PY%" scripts\pause_project.py 15

"%PY%" scripts\assemble_r15_direct.py 15

pause

