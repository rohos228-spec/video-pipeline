# Shared helpers for fleet .env / manifest scripts.
function Get-RepoRoot {
    param([string]$ScriptRoot = $PSScriptRoot)
    return (Resolve-Path (Join-Path $ScriptRoot "..")).Path
}

function Get-TailscaleExe {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "${env:ProgramFiles}\Tailscale\tailscale.exe",
        "${env:ProgramFiles(x86)}\Tailscale\tailscale.exe",
        "$env:LOCALAPPDATA\Tailscale\tailscale.exe"
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}

function Invoke-Tailscale {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $exe = Get-TailscaleExe
    if (-not $exe) { throw "tailscale.exe not found. Open Tailscale app from Start menu and log in." }
    & $exe @Args
    return $LASTEXITCODE
}

function Get-TailscaleIp4 {
    if (-not (Get-TailscaleExe)) { return $null }
    try {
        $ip = (& (Get-TailscaleExe) ip -4 2>$null | Select-Object -First 1).ToString().Trim()
        if ($ip -match '^\d+\.\d+\.\d+\.\d+$') { return $ip }
    } catch { }
    return $null
}

function Read-DotEnv {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        $idx = $t.IndexOf("=")
        if ($idx -lt 1) { continue }
        $k = $t.Substring(0, $idx).Trim()
        $v = $t.Substring($idx + 1).Trim()
        $map[$k] = $v
    }
    return $map
}

function Set-EnvLine {
    param(
        [ref]$Text,
        [string]$Key,
        [string]$Value
    )
    $escaped = [regex]::Escape($Key)
    if ($Text.Value -match "(?m)^$escaped=") {
        $Text.Value = [regex]::Replace($Text.Value, "(?m)^$escaped=.*", "$Key=$Value")
    } else {
        if ($Text.Value -and -not $Text.Value.EndsWith("`n")) { $Text.Value += "`n" }
        $Text.Value += "$Key=$Value`n"
    }
}

function Write-DotEnv {
    param(
        [string]$Path,
        [hashtable]$Values
    )
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $lines = @()
    foreach ($k in ($Values.Keys | Sort-Object)) {
        $lines += "$k=$($Values[$k])"
    }
    Set-Content -LiteralPath $Path -Value ($lines -join "`n") -Encoding UTF8
}
