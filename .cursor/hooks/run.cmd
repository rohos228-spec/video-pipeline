: << 'CMDBLOCK'
@echo off
REM Polyglot hook launcher (Windows cmd + Unix sh), modelled on superpowers/hooks/run-hook.cmd.
REM Usage: run.cmd <script.py>
REM Python lookup: project .venv -> py -3 -> python -> python3.
setlocal
set "HOOK_DIR=%~dp0"
set "ROOT=%HOOK_DIR%..\.."
set "PYEXE="
set "PYARG="
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYEXE=%ROOT%\.venv\Scripts\python.exe"
if not defined PYEXE (
    where py >nul 2>nul && (set "PYEXE=py" & set "PYARG=-3")
)
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    where python3 >nul 2>nul && set "PYEXE=python3"
)
if not defined PYEXE (
    echo {"permission":"deny","continue":true,"user_message":"vp-hooks: Python not found, action blocked. Install Python 3.11+ or create .venv","agent_message":"vp-hooks: Python not found"}
    exit /b 2
)
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if defined PYARG (
    "%PYEXE%" %PYARG% "%HOOK_DIR%%~1"
) else (
    "%PYEXE%" "%HOOK_DIR%%~1"
)
exit /b %ERRORLEVEL%
CMDBLOCK

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
for PY in "$ROOT/.venv/bin/python" python3 python; do
    if [ -x "$PY" ] || command -v "$PY" >/dev/null 2>&1; then
        exec "$PY" "$DIR/$1"
    fi
done
echo '{"permission":"deny","continue":true,"user_message":"vp-hooks: Python not found, action blocked. Install Python 3.11+ or create .venv","agent_message":"vp-hooks: Python not found"}'
exit 2
