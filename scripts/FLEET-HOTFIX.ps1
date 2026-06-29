# Fleet hotfix: patch hub .env + restart backend (no git).
param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

Write-Host "=== FLEET HOTFIX (no git) ===" -ForegroundColor Cyan

$HubIp = Get-TailscaleIp4
if (-not $HubIp) { $HubIp = "100.72.202.35" }
$hubUrl = "http://${HubIp}:8765"

$EnvPath = Join-Path $Root ".env"
$text = if (Test-Path $EnvPath) { Get-Content $EnvPath -Raw -Encoding UTF8 } else { "" }
$patch = @{
    "FLEET_ENABLED"       = "true"
    "FLEET_ROLE"          = "hub"
    "FLEET_MONTAGE_HUB"   = "true"
    "FLEET_HUB_IS_WORKER" = "true"
    "FLEET_AUTO_PULL"     = "true"
    "WEB_HOST"            = "0.0.0.0"
    "FLEET_PUBLIC_URL"    = $hubUrl
    "FLEET_HUB_URL"       = $hubUrl
}
foreach ($kv in $patch.GetEnumerator()) {
    Set-EnvLine -Text ([ref]$text) -Key $kv.Key -Value $kv.Value
}
Set-Content -LiteralPath $EnvPath -Value $text.TrimEnd() -Encoding UTF8
Write-Host ".env fleet URL: $hubUrl" -ForegroundColor Green

Write-Host "Building web UI..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "web")
& npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
Pop-Location

& (Join-Path $Root "scripts\stop-backend.ps1") -Quiet
Start-Sleep -Seconds 2

$applyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "apply-local.ps1"), "-SkipBuild")
if ($NoBrowser) { $applyArgs += "-NoBrowser" }
& powershell.exe @applyArgs
exit $LASTEXITCODE
