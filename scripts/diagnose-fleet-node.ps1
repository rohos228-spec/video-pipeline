# Диагностика связи hub -> worker (таймаут / Cannot connect).
param(
    [string]$Url = "http://100.123.109.94:8765",
    [string]$NodeName = ""
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "FleetEnv.ps1")
$Root = Get-RepoRoot -ScriptRoot $PSScriptRoot

if ($NodeName -and -not $Url) {
    $envPath = Join-Path $Root ".env"
    # best-effort: read from hub API if studio up
    try {
        $nodes = Invoke-RestMethod "http://127.0.0.1:8765/api/fleet/nodes" -TimeoutSec 5
        $n = $nodes | Where-Object { $_.name -eq $NodeName } | Select-Object -First 1
        if ($n) { $Url = $n.base_url }
    } catch { }
}

if (-not $Url) {
    Write-Host "Usage: .\scripts\diagnose-fleet-node.ps1 -Url http://100.x.x.x:8765" -ForegroundColor Yellow
    exit 1
}

$Url = $Url.Trim().TrimEnd("/")
$m = [regex]::Match($Url, "^https?://([^:/]+)(?::(\d+))?")
if (-not $m.Success) {
    Write-Host "Bad URL: $Url" -ForegroundColor Red
    exit 1
}
$hostName = $m.Groups[1].Value
$port = if ($m.Groups[2].Success) { [int]$m.Groups[2].Value } else { 8765 }

Write-Host "=== Fleet node diagnose ===" -ForegroundColor Cyan
Write-Host "Target: $Url ($hostName`:$port)" -ForegroundColor White
Write-Host ""

Write-Host "[1] Tailscale on THIS PC" -ForegroundColor Yellow
$ts = Get-TailscaleExe
if (-not $ts) {
    Write-Host "  FAIL: tailscale.exe not found" -ForegroundColor Red
} else {
    $myIp = Get-TailscaleIp4
    Write-Host "  my IP: $(if ($myIp) { $myIp } else { '(not connected)' })" -ForegroundColor $(if ($myIp) { "Green" } else { "Red" })
    if ($hostName -match '^\d+\.\d+\.\d+\.\d+$') {
        Write-Host "  ping $hostName ..." -ForegroundColor Gray
        & $ts ping $hostName 2>&1 | ForEach-Object { Write-Host "    $_" }
    }
}

Write-Host ""
Write-Host "[2] TCP port $port -> $hostName" -ForegroundColor Yellow
try {
    $tcp = Test-NetConnection -ComputerName $hostName -Port $port -WarningAction SilentlyContinue
    if ($tcp.TcpTestSucceeded) {
        Write-Host "  OK: port open" -ForegroundColor Green
    } else {
        Write-Host "  FAIL: port closed or filtered (firewall / Studio not running / WEB_HOST=127.0.0.1)" -ForegroundColor Red
    }
} catch {
    Write-Host "  FAIL: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "[3] HTTP /api/health" -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 8
    Write-Host "  OK: $($health | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "  FAIL: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "[4] HTTP /api/fleet/local/info (fleet token)" -ForegroundColor Yellow
$token = $null
$secrets = Join-Path $Root "fleet\secrets.env"
if (Test-Path $secrets) {
    $map = Read-DotEnv -Path $secrets
    $token = $map["FLEET_AGENT_TOKEN"]
}
if (-not $token) {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        $line = Select-String -Path $envFile -Pattern '^\s*FLEET_AGENT_TOKEN=(.+)$' | Select-Object -Last 1
        if ($line) { $token = $line.Matches.Groups[1].Value.Trim() }
    }
}
$headers = @{}
if ($token) { $headers.Authorization = "Bearer $token" }
try {
    $info = Invoke-RestMethod -Uri "$Url/api/fleet/local/info" -Headers $headers -TimeoutSec 8
    Write-Host "  OK: $($info.name) @ $($info.hostname)" -ForegroundColor Green
} catch {
    Write-Host "  FAIL: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $token) {
        Write-Host "  (no FLEET_AGENT_TOKEN in .env / fleet/secrets.env)" -ForegroundColor DarkYellow
    }
}

Write-Host ""
Write-Host "=== Fix on WORKER ($NodeName / $hostName) ===" -ForegroundColor Cyan
Write-Host "  1. Studio running (run-backend.ps1 window, Uvicorn on :8765)"
Write-Host "  2. .env: WEB_HOST=0.0.0.0   FLEET_PUBLIC_URL=$Url"
Write-Host "  3. Admin once: powershell -ExecutionPolicy Bypass -File .\scripts\allow-studio-firewall.ps1"
Write-Host "  4. On worker: curl http://127.0.0.1:8765/api/health"
Write-Host "  5. netstat -an | findstr 8765  -> must show 0.0.0.0:8765 LISTENING"
