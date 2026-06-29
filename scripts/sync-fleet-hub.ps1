# Hub: fleet branch + build UI + Tailscale .env + restart Studio.
param([switch]$NoStart)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot
Set-Location $Root

$Branch = "feature/fleet-montage-queue-v161"

Write-Host "=== Hub sync: $Branch ===" -ForegroundColor Cyan

$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
git fetch origin $Branch 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
git checkout -B $Branch "origin/$Branch" 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    git checkout -B $Branch 2>&1 | Out-Host
    git reset --hard "origin/$Branch" 2>&1 | Out-Host
}
if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }
$ErrorActionPreference = $prevEap

$head = (git rev-parse --short HEAD).Trim()
Write-Host "Git: $head on $(git branch --show-current)" -ForegroundColor Green

$HubIp = Get-TailscaleIp4
if (-not $HubIp) { $HubIp = "100.72.202.35" }
$hubUrl = "http://${HubIp}:8765"

$EnvPath = Join-Path $Root ".env"
$text = if (Test-Path $EnvPath) { Get-Content $EnvPath -Raw -Encoding UTF8 } else { "" }
Set-EnvLine -Text ([ref]$text) -Key "FLEET_ENABLED" -Value "true"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_ROLE" -Value "hub"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_MONTAGE_HUB" -Value "true"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_HUB_IS_WORKER" -Value "true"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_AUTO_PULL" -Value "true"
Set-EnvLine -Text ([ref]$text) -Key "FLEET_PUBLIC_URL" -Value $hubUrl
Set-EnvLine -Text ([ref]$text) -Key "FLEET_HUB_URL" -Value $hubUrl
Set-EnvLine -Text ([ref]$text) -Key "WEB_HOST" -Value "0.0.0.0"
Set-Content -LiteralPath $EnvPath -Value $text.TrimEnd() -Encoding UTF8
Write-Host ".env hub URL: $hubUrl" -ForegroundColor Green

Write-Host "Building web UI..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "web")
& npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
Pop-Location

& (Join-Path $Root "scripts\stop-backend.ps1") -Quiet
Start-Sleep -Seconds 2

$fw = Join-Path $Root "scripts\allow-studio-firewall.ps1"
if (Test-Path $fw) {
    Write-Host "Opening firewall port 8765 (needs Admin once)..." -ForegroundColor Cyan
    try {
        & powershell -ExecutionPolicy Bypass -File $fw
    } catch {
        Write-Host "Firewall: run as Admin: .\scripts\allow-studio-firewall.ps1" -ForegroundColor Yellow
    }
}

$verLine = (Get-Content (Join-Path $Root "web\STUDIO_VERSION") -TotalCount 1).Trim()
Write-Host "Done. STUDIO_VERSION build=$verLine git=$head" -ForegroundColor Green

if (-not $NoStart) {
    & (Join-Path $Root "RUN-STUDIO.ps1")
}
