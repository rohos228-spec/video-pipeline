# Install Tailscale via winget (Windows). Already installed = OK.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")

if (Get-TailscaleExe) {
    Write-Host "Tailscale already installed: $(Get-TailscaleExe)" -ForegroundColor Green
    exit 0
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget not found. Install from: https://tailscale.com/download/windows"
}

Write-Host "Installing Tailscale..." -ForegroundColor Cyan
& winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
$code = $LASTEXITCODE

if (Get-TailscaleExe) {
    Write-Host "Tailscale ready: $(Get-TailscaleExe)" -ForegroundColor Green
    exit 0
}

# winget often returns non-zero when package exists but CLI not in PATH yet
$okCodes = @(0, -1978335189, 2316632107)
if ($okCodes -contains $code) {
    Write-Host "Tailscale package present. Open Tailscale from Start menu if CLI missing." -ForegroundColor Yellow
    exit 0
}

throw "winget install Tailscale failed (exit $code). Install manually: https://tailscale.com/download/windows"
