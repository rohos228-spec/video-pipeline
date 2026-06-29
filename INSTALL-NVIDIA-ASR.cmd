@echo off
setlocal
cd /d "%~dp0"

set PY=%~dp0.venv\Scripts\python.exe
set WHEELDIR=%~dp0data\wheels\torch-cu128
set TORCH_WHL=%WHEELDIR%\torch-2.7.1+cu128-cp311-cp311-win_amd64.whl
set AUDIO_WHL=%WHEELDIR%\torchaudio-2.7.1+cu128-cp311-cp311-win_amd64.whl

if not exist "%PY%" (
  echo FAIL: no .venv - run install.ps1 first
  pause
  exit /b 1
)

echo === Step 1: nvidia-smi ===
nvidia-smi
if errorlevel 1 goto fail

echo.
echo === Step 2: pip upgrade ===
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto fail

echo.
"%PY%" -c "import torch; import torch.cuda; exit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
if errorlevel 1 (
  if exist "%TORCH_WHL%" if exist "%AUDIO_WHL%" (
    echo === Step 3: install torch from local wheels ===
    "%PY%" -m pip install "%TORCH_WHL%" "%AUDIO_WHL%"
    if errorlevel 1 goto fail
  ) else (
    echo === Step 3: no local wheels - run DOWNLOAD-TORCH-CUDA.cmd first ===
    echo      pip CDN often fails from RU - use VPN + DOWNLOAD-TORCH-CUDA.cmd
    goto fail
  )
) else (
  echo === Step 3: torch+cuda already OK ===
)

echo.
echo === Step 4: CUDA check ===
"%PY%" -c "import torch; x=torch.zeros(1,device='cuda'); torch.cuda.synchronize(); print('torch', torch.__version__); print('gpu', torch.cuda.get_device_name(0)); print('sm', torch.cuda.get_device_capability(0))"
if errorlevel 1 goto fail

echo.
echo === Step 5: NeMo ASR ===
"%PY%" -m pip install --default-timeout=900 --retries 15 -e ".[nvidia-asr]"
if errorlevel 1 goto fail

echo.
echo === Step 6: preload model ===
set ASR_BACKEND=nvidia
"%PY%" scripts\download_whisper.py
if errorlevel 1 goto fail

echo.
echo OK. Run GO-MONTAGE-15.cmd
pause
exit /b 0

:fail
echo.
echo FAIL.
echo 1^) VPN on
echo 2^) DOWNLOAD-TORCH-CUDA.cmd  - wait until OK
echo 3^) INSTALL-NVIDIA-ASR.cmd again
pause
exit /b 1
