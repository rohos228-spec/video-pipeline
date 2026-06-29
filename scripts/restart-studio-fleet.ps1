# Restart Studio after fleet/UI update — patches Tailscale URL in .env
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
. (Join-Path $Root "scripts\FleetEnv.ps1")

Write-Host "==> patch .env" -ForegroundColor Cyan
$hubIp = Get-TailscaleIp4
if (-not $hubIp) { $hubIp = "100.72.202.35" }
$hubUrl = "http://${hubIp}:8765"

$p = Join-Path $Root ".env"
$t = if (Test-Path $p) { Get-Content $p -Raw -Encoding UTF8 } else { "" }
$patch = @{
    "FLEET_HUB_IS_WORKER" = "true"
    "FLEET_ENABLED"       = "true"
    "FLEET_ROLE"          = "hub"
    "FLEET_MONTAGE_HUB"   = "true"
    "FLEET_AUTO_PULL"     = "true"
    "WEB_HOST"            = "0.0.0.0"
    "FLEET_PUBLIC_URL"    = $hubUrl
    "FLEET_HUB_URL"       = $hubUrl
}
foreach ($kv in $patch.GetEnumerator()) {
    Set-EnvLine -Text ([ref]$t) -Key $kv.Key -Value $kv.Value
}
Set-Content $p $t.TrimEnd() -Encoding UTF8
Write-Host "    FLEET_PUBLIC_URL=$hubUrl" -ForegroundColor Green

Write-Host "==> build web UI" -ForegroundColor Cyan
Push-Location (Join-Path $Root "web")
npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
Pop-Location

Write-Host "==> stop old backend" -ForegroundColor Cyan
$stop = Join-Path $Root "scripts\stop-backend.ps1"
if (Test-Path $stop) { & $stop -Quiet; Start-Sleep 2 }

$fw = Join-Path $Root "scripts\allow-studio-firewall.ps1"
if (Test-Path $fw) {
    Write-Host "==> firewall 8765 (Admin once if prompted)" -ForegroundColor Cyan
    try { & powershell -ExecutionPolicy Bypass -File $fw } catch { }
}

Write-Host "==> start Studio (DO NOT CLOSE this window)" -ForegroundColor Green
& (Join-Path $Root "RUN-STUDIO.ps1")
