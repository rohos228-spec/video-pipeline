# NucBox: push project 17 to hub
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$token = ""
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^\s*FLEET_AGENT_TOKEN=(.+)$' | Select-Object -Last 1
    if ($m) { $token = $m.Matches.Groups[1].Value.Trim() }
}
if (-not $token) { Write-Host "FAIL: FLEET_AGENT_TOKEN not in .env" -ForegroundColor Red; exit 1 }

$headers = @{ Authorization = "Bearer $token" }
Write-Host "Otpravka #17 na hub (10-20 min)..." -ForegroundColor Cyan
Write-Host "  Progres v loge backend: push-to-hub upload XX%" -ForegroundColor Yellow
Write-Host "  Log: data\studio-live.log (Get-Content -Wait -Tail 20 ...)" -ForegroundColor DarkGray
try {
    $r = Invoke-RestMethod -Method POST `
        -Uri "http://127.0.0.1:8765/api/fleet/local/projects/17/push-to-hub" `
        -Headers $headers -TimeoutSec 7200
    Write-Host ("OK slug=" + $r.slug + " queued=" + $r.queued) -ForegroundColor Green
} catch {
    Write-Host ("FAIL: " + $_.Exception.Message) -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red }
    exit 1
}
