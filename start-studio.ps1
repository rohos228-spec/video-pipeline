# Alias -> RUN-STUDIO.ps1
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "RUN-STUDIO.ps1") @args
exit $LASTEXITCODE
