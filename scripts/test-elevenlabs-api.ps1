# Проверка ElevenLabs API key из .env (+ SOCKS5/HTTP proxy, backend)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

function Read-EnvValue([string]$Name) {
    $envFile = Join-Path $repo ".env"
    if (-not (Test-Path $envFile)) { return $null }
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.+)\s*$") {
            $v = $Matches[1].Trim().Trim('"').Trim("'")
            if ($v) { return $v }
        }
    }
    return $null
}

$key = Read-EnvValue "ELEVENLABS_API_KEY"
$proxyUrl = Read-EnvValue "ELEVENLABS_PROXY_URL"
if (-not $proxyUrl) { $proxyUrl = Read-EnvValue "TELEGRAM_PROXY_URL" }

if (-not $key) {
    Write-Host "ELEVENLABS_API_KEY не найден в .env" -ForegroundColor Red
    exit 1
}

$keyHint = if ($key.Length -gt 10) { $key.Substring(0, 8) + "…" } else { "???" }
Write-Host "Ключ: $keyHint" -ForegroundColor Cyan
if ($proxyUrl) {
    Write-Host "Proxy: $proxyUrl" -ForegroundColor Cyan
} else {
    Write-Host "Proxy не задан — добавь ELEVENLABS_PROXY_URL=socks5://user:pass@host:8000" -ForegroundColor Yellow
}

Write-Host "`n==> Python connect_by_ip (SOCKS5 поддерживается)" -ForegroundColor Yellow
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Нет .venv — сначала install.ps1" -ForegroundColor Red
    exit 1
}
$code = @'
import asyncio, json, traceback
from app.services.elevenlabs_api import connect_by_ip
try:
    r = asyncio.run(connect_by_ip())
    print("OK", json.dumps({k: r[k] for k in ("voice_count","connection_mode","key_hint","proxy") if k in r}, ensure_ascii=False))
except Exception as e:
    print("FAIL", e)
    traceback.print_exc()
    raise SystemExit(1)
'@
& $py -c $code
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n==> backend /api/elevenlabs/check-env" -ForegroundColor Yellow
try {
    $r = Invoke-RestMethod "http://127.0.0.1:8765/api/elevenlabs/check-env" -TimeoutSec 15
    Write-Host ("OK backend — голосов {0}, {1}" -f $r.voice_count, $r.connection_mode) -ForegroundColor Green
} catch {
    if ($_.Exception.Message -match "Unable to connect|actively refused") {
        Write-Host "Backend не запущен — .\start-backend.cmd" -ForegroundColor DarkYellow
    } else {
        Write-Host "Backend FAIL: $($_.ErrorDetails.Message)" -ForegroundColor Red
        exit 1
    }
}
