@echo off
cd /d "%~dp0"
set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo === MONTAGE VARIANT 2: overlay on black, Excel R15 ===
"%PY%" scripts\assemble_r15_direct.py 15
if errorlevel 1 (
  echo FAIL
  pause
  exit /b 1
)
"%PY%" scripts\verify_final_engine.py 15
pause
