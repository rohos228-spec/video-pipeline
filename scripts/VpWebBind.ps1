# Чтение/запись WEB_HOST и авто-настройка fleet (0.0.0.0).

function Get-VpEnvFilePath {
    param([string]$Root)
    Join-Path $Root ".env"
}

function Get-VpEnvFileValue {
    param(
        [string]$Root,
        [string]$Key,
        [string]$Default = ""
    )
    $envFile = Get-VpEnvFilePath -Root $Root
    if (-not (Test-Path -LiteralPath $envFile)) { return $Default }
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Key))=(.*)$") {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $Default
}

function Set-VpEnvFileValue {
    param(
        [string]$Root,
        [string]$Key,
        [string]$Value
    )
    $envFile = Get-VpEnvFilePath -Root $Root
    $lines = @()
    $found = $false
    if (Test-Path -LiteralPath $envFile) {
        $lines = @(Get-Content -LiteralPath $envFile -Encoding UTF8)
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*$([regex]::Escape($Key))=") {
                $lines[$i] = "$Key=$Value"
                $found = $true
            }
        }
    }
    if (-not $found) {
        if ($lines.Count -gt 0 -and $lines[-1].Trim() -ne "") {
            $lines += ""
        }
        $lines += "$Key=$Value"
    }
    Set-Content -LiteralPath $envFile -Value $lines -Encoding UTF8
}

function Get-VpWebBindConfig {
    param([string]$Root)
    $fleetEnabled = (Get-VpEnvFileValue -Root $Root -Key "FLEET_ENABLED" -Default "false").ToLowerInvariant()
    $fleetOn = $fleetEnabled -in @("true", "1", "yes", "on")
    $defaultHost = if ($fleetOn) { "0.0.0.0" } else { "127.0.0.1" }
    $webHost = if ($env:WEB_HOST) { $env:WEB_HOST.Trim() } else { Get-VpEnvFileValue -Root $Root -Key "WEB_HOST" -Default $defaultHost }
    $webPort = if ($env:WEB_PORT) { $env:WEB_PORT.Trim() } else { Get-VpEnvFileValue -Root $Root -Key "WEB_PORT" -Default "8765" }
    @{
        WebHost = $webHost
        WebPort = $webPort
        FleetEnabled = $fleetOn
        LocalUrl = "http://127.0.0.1:$webPort"
    }
}

function Get-VpTailscaleIPv4 {
    try {
        $out = & tailscale ip -4 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return "" }
        $ip = ($out | Select-Object -First 1).ToString().Trim()
        if ($ip -match '^\d+\.\d+\.\d+\.\d+$') { return $ip }
    } catch { }
    return ""
}

function Ensure-VpFleetNetworkEnv {
    param([string]$Root)
    $changed = @()
    $fleetEnabled = (Get-VpEnvFileValue -Root $Root -Key "FLEET_ENABLED" -Default "false").ToLowerInvariant()
    if ($fleetEnabled -notin @("true", "1", "yes", "on")) {
        return $changed
    }

    $currentHost = Get-VpEnvFileValue -Root $Root -Key "WEB_HOST" -Default ""
    if ($currentHost -ne "0.0.0.0") {
        Set-VpEnvFileValue -Root $Root -Key "WEB_HOST" -Value "0.0.0.0"
        $changed += "WEB_HOST=0.0.0.0"
    }

    $role = (Get-VpEnvFileValue -Root $Root -Key "FLEET_ROLE" -Default "hub").ToLowerInvariant()
    if ($role -eq "agent") {
        $isMain = (Get-VpEnvFileValue -Root $Root -Key "FLEET_IS_MAIN" -Default "").ToLowerInvariant()
        if ($isMain -in @("true", "1", "yes", "on")) {
            Set-VpEnvFileValue -Root $Root -Key "FLEET_IS_MAIN" -Value "false"
            $changed += "FLEET_IS_MAIN=false"
        }
    }

    $pub = Get-VpEnvFileValue -Root $Root -Key "FLEET_PUBLIC_URL" -Default ""
    if (-not $pub -or $pub -match "127\.0\.0\.1|localhost") {
        $tsIp = Get-VpTailscaleIPv4
        if ($tsIp) {
            $webPort = Get-VpEnvFileValue -Root $Root -Key "WEB_PORT" -Default "8765"
            $newPub = "http://${tsIp}:$webPort"
            Set-VpEnvFileValue -Root $Root -Key "FLEET_PUBLIC_URL" -Value $newPub
            $changed += "FLEET_PUBLIC_URL=$newPub"
            $pub = $newPub
        } else {
            Write-Warning "FLEET_PUBLIC_URL не задан или localhost — укажите Tailscale IP:8765 в .env (tailscale ip -4)"
        }
    }

    $nodeName = Get-VpEnvFileValue -Root $Root -Key "FLEET_NODE_NAME" -Default ""
    if (-not $nodeName) {
        $defaultName = if ($role -eq "agent") { "child-pc" } else { "main-pc" }
        Set-VpEnvFileValue -Root $Root -Key "FLEET_NODE_NAME" -Value $defaultName
        $changed += "FLEET_NODE_NAME=$defaultName"
    }

    return $changed
}

function Get-VpListenAddresses {
    param([int]$Port = 8765)
    try {
        return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty LocalAddress -Unique)
    } catch {
        return @()
    }
}

function Test-VpListeningAllInterfaces {
    param([int]$Port = 8765)
    $addrs = Get-VpListenAddresses -Port $Port
    return ($addrs -contains "0.0.0.0") -or ($addrs -contains "::")
}
