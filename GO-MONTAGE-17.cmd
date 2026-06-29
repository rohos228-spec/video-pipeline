@echo off
cd /d "%~dp0"
set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo === MONTAGE #17 — параллельно с Outsee, Studio не трогать ===
echo.
"%PY%" scripts\assemble_r15_direct.py 17
if errorlevel 1 (
  echo FAIL — см. вывод выше
  pause
  exit /b 1
)
if exist "%PY%" "%PY%" scripts\verify_final_engine.py 17
echo.
echo OK — data\videos\nicshe\final\nicshe.mp4
pause
