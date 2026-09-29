param(
    [Parameter(Mandatory=$true)][string]$KeySourcePath,
    [Parameter(Mandatory=$true)][string]$ResultsRoot,
    [switch]$OnlineAuthorized
)
$ErrorActionPreference = 'Stop'
if (-not $OnlineAuthorized) { throw 'A new explicit online authorization is required; no POST' }
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$root = [IO.Path]::GetFullPath((Join-Path $source '..\..'))
$results = [IO.Path]::GetFullPath($ResultsRoot)
if (Test-Path -LiteralPath $results) { throw 'Existing results directory: refusing to reuse an execution' }
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$dockerConfig = Join-Path $results 'empty-docker-config'
$stage = Join-Path $results 'app-source'
$gatewayStage = Join-Path $results 'gateway-source'
$appResults = Join-Path $results 'app-results'
$gatewayResults = Join-Path $results 'gateway-results'
$safe = 'safe.directory=' + $source.Replace('\','/')
if ((& git -c $safe -C $source status --porcelain)) { throw 'Source worktree is not clean' }
$sha = & git -c $safe -C $source rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify source version' }
New-Item -ItemType Directory -Force -Path $dockerConfig,$stage,$gatewayStage,$appResults,$gatewayResults | Out-Null
$allowed = @(& git -c $safe -c core.quotepath=false -C $source ls-files app prompts templates pyproject.toml)
if ($LASTEXITCODE -ne 0 -or -not $allowed) { throw 'Cannot enumerate source' }
$allowed += @('scripts/diagnostics/decision_tool_cycle_entry.py')
$manifest = @{}
foreach ($relative in $allowed) {
    if ($relative -match '(^|/)\.env($|\.)|(^|/)\.git(/|$)|\.(db|sqlite|pem|key)$') { throw 'Forbidden staged file' }
    $from = Join-Path $source $relative
    $to = Join-Path $stage $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
    Copy-Item -LiteralPath $from -Destination $to
    $manifest[$relative.Replace('\','/')] = (Get-FileHash $to -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $results 'source-manifest.json')
Copy-Item (Join-Path $source 'scripts\diagnostics\gateway.py') (Join-Path $gatewayStage 'gateway.py')
Copy-Item (Join-Path $source 'scripts\diagnostics\decision_tool_cycle_gateway.py') (Join-Path $gatewayStage 'decision_tool_cycle_gateway.py')
Copy-Item (Join-Path $source 'scripts\diagnostics\gateway_egress_probe.py') (Join-Path $gatewayStage 'gateway_egress_probe.py')
Copy-Item (Join-Path $source 'config\decision-tool-cycle.example.json') (Join-Path $gatewayStage 'pilot-config.json')
$gatewayHashes = @{}
foreach ($item in @('gateway.py','decision_tool_cycle_gateway.py','gateway_egress_probe.py','pilot-config.json')) {
    $gatewayHashes[$item] = (Get-FileHash (Join-Path $gatewayStage $item) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$gatewayHashes | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $results 'gateway-source-hashes.json')
$suffix = [guid]::NewGuid().ToString('N').Substring(0,12)
$app = 'vp-decision-online-app-' + $suffix
$gateway = 'vp-decision-online-gateway-' + $suffix
$helper = 'vp-decision-egress-helper-' + $suffix
$counterHelper = 'vp-decision-egress-counter-' + $suffix
$resolver = 'vp-decision-egress-resolver-' + $suffix
$volume = 'vp-decision-online-socket-' + $suffix
$dc = @('--config',$dockerConfig,'--host','npipe:////./pipe/dockerDesktopLinuxEngine')
& $docker @dc info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Engine unavailable; no POST' }
$image='vp-offline-diagnostic:20260924'
$nftImage='vp-offline-diagnostic-nft:20260929'
$expectedNftImageId='sha256:658b2b1f02cc30ed77752f96ff15809f6ac731aab21c7cedcfef160d8c089ecb'
$actualNftImageId=& $docker @dc image inspect $nftImage --format '{{.Id}}'
if ($LASTEXITCODE -ne 0 -or $actualNftImageId -ne $expectedNftImageId) { throw 'Unverified nft diagnostic image; no key read' }
$answers=@(& $docker @dc run --pull never --name $resolver --network bridge --cap-drop ALL --security-opt no-new-privileges $nftImage getent ahostsv4 api.kie.ai)
if ($LASTEXITCODE -ne 0 -or -not $answers) { throw 'Docker DNS resolution failed; no key read' }
$pinnedIp=($answers[0] -split '\s+')[0]
$ipObject=$null
if (-not [Net.IPAddress]::TryParse($pinnedIp,[ref]$ipObject) -or $ipObject.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'Invalid pinned IPv4; no key read' }
@{utc=(Get-Date).ToUniversalTime().ToString('o');method='Docker bridge getent ahostsv4';hostname='api.kie.ai';ipv4=$pinnedIp} |
  ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $results 'dns-pin.json')
$policyDir=Join-Path $results 'network-policy'
New-Item -ItemType Directory -Path $policyDir | Out-Null
$policyTemplate=[IO.File]::ReadAllText((Join-Path $source 'scripts\diagnostics\gateway-egress.nft.in'))
$policyText=$policyTemplate.Replace('@PINNED_IPV4@',$pinnedIp).Replace("`r`n","`n")
[IO.File]::WriteAllText((Join-Path $policyDir 'gateway-egress.nft'),$policyText,(New-Object System.Text.UTF8Encoding($false)))
& $docker @dc volume create $volume | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated socket volume' }
& $docker @dc create --pull never --name $gateway --network bridge --add-host "api.kie.ai:$pinnedIp" --cap-drop ALL --security-opt no-new-privileges --mount "type=volume,src=$volume,dst=/gateway" --mount "type=bind,src=$gatewayResults,dst=/results" $nftImage sleep infinity | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot create gateway; no POST' }
& $docker @dc create --pull never -i --name $app --network none --cap-drop ALL --security-opt no-new-privileges --mount "type=volume,src=$volume,dst=/gateway" --mount "type=bind,src=$appResults,dst=/results" $image python /observe-source/scripts/diagnostics/decision_tool_cycle_entry.py | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated app; no POST' }
& $docker @dc cp "$gatewayStage/." "${gateway}:/gateway-source"
if ($LASTEXITCODE -ne 0) { throw 'Cannot stage gateway; no POST' }
& $docker @dc cp "$stage/." "${app}:/observe-source"
if ($LASTEXITCODE -ne 0) { throw 'Cannot stage source; no POST' }
$seed = Join-Path $root 'outputs\actions-v1-final\recovery-a-08\artifacts\exhausted-checkpoint'
& $docker @dc cp "$seed/." "${app}:/observe-denial-seed"
if ($LASTEXITCODE -ne 0) { throw 'Cannot stage synthetic seed; no POST' }
$provenance = Join-Path $root 'outputs\actions-v1-final\recovery-a-08\artifacts\checkpoint-provenance.json'
& $docker @dc cp $provenance "${app}:/observe-checkpoint-provenance.json"
if ($LASTEXITCODE -ne 0) { throw 'Cannot stage provenance; no POST' }
@{source_commit=$sha;app=$app;gateway=$gateway;socket_volume=$volume;model='gpt-5-6-sol';
  endpoint='https://api.kie.ai/codex/v1/responses';max_requests=3;max_output_tokens=1024;
  app_network='none';gateway_network='bridge-with-nft-default-drop';pinned_ipv4=$pinnedIp;
  nft_image_id=$actualNftImageId;key_in_files=$false;key_in_env=$false} |
  ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $results 'run-metadata.json')
& $docker @dc start $gateway | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Gateway keyless preparation start failed; no POST' }
& $docker @dc create --pull never --name $helper --network "container:$gateway" --cap-drop ALL --cap-add NET_ADMIN --security-opt no-new-privileges $nftImage sh -c 'nft -f /policy/gateway-egress.nft && nft -j list chain inet decision_gateway_egress output' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Keyless nft helper creation failed; no key read' }
& $docker @dc cp $policyDir "${helper}:/policy"
if ($LASTEXITCODE -ne 0) { throw 'Cannot stage nft policy; no key read' }
$installed=@(& $docker @dc start -a $helper 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'nft policy installation failed; no key read' }
$installed | Set-Content -Encoding UTF8 (Join-Path $results 'nft-installed.json')
& $docker @dc exec -d --env "PINNED_KIE_IPV4=$pinnedIp" $gateway python /gateway-source/decision_tool_cycle_gateway.py
if ($LASTEXITCODE -ne 0) { throw 'Gateway process did not start; no key read' }
$ready=$false
for ($i=0;$i -lt 40;$i++) {
    & $docker @dc exec $gateway test -S /gateway/llm.sock 2>$null
    if ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $gatewayResults 'gateway-network-probe.json'))) { $ready=$true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw 'Gateway network probe or socket unavailable; no key read' }
$probe=Get-Content -Raw (Join-Path $gatewayResults 'gateway-network-probe.json') | ConvertFrom-Json
if ($probe.pinned_ipv4 -ne $pinnedIp -or -not $probe.tls_hostname_verified -or -not $probe.nft_change_denied -or
    $probe.capabilities.CapEff -ne '0000000000000000' -or $probe.capabilities.CapBnd -ne '0000000000000000' -or
    -not $probe.controlled_listener_active -or $probe.gateway_direct_connect_errno -eq 0 -or
    $probe.child_direct_connect_errno -eq 0 -or $probe.docker_dns_connect_errno -eq 0 -or $probe.http_requests -ne 0) {
    throw 'Gateway direct-socket/TLS/capability probe failed; no key read'
}
$counters=@(& $docker @dc run --pull never --name $counterHelper --network "container:$gateway" --cap-drop ALL --cap-add NET_ADMIN --security-opt no-new-privileges $nftImage nft -j list chain inet decision_gateway_egress output)
if ($LASTEXITCODE -ne 0) { throw 'Cannot attest nft policy; no key read' }
$counters | Set-Content -Encoding UTF8 (Join-Path $results 'nft-verified.json')
$rules=($counters -join "`n" | ConvertFrom-Json -Depth 20).nftables
$chain=@($rules | Where-Object {$_.chain})
$entries=@($rules | Where-Object {$_.rule})
if ($chain.Count -ne 1 -or $chain[0].chain.policy -ne 'drop' -or $entries.Count -ne 2 -or
    $entries[0].rule.expr[0].match.right -ne $pinnedIp -or
    $entries[0].rule.expr[1].match.right -ne 443 -or
    $entries[0].rule.expr[2].counter.packets -lt 1 -or
    $entries[1].rule.expr[0].counter.packets -lt 3 -or
    -not ($entries[0].rule.expr[3].PSObject.Properties.Name -contains 'accept') -or
    -not ($entries[1].rule.expr[1].PSObject.Properties.Name -contains 'drop')) {
    throw 'nft rule/counter attestation failed; no key read'
}
if (-not (Test-Path -LiteralPath $KeySourcePath)) { throw 'Authorized key source absent after network preflight; no POST' }
$key=$null
foreach ($line in [IO.File]::ReadLines($KeySourcePath)) {
    if ($line -match '^GPT_API_KEY\s*=\s*(.+?)\s*$') {
        $candidate=$Matches[1].Trim().Trim('"').Trim("'")
        if ($candidate) { $key=$candidate }
    }
}
if (-not $key -or $key.Length -lt 12) { throw 'GPT_API_KEY unavailable; no POST' }
# The key enters only the app process stdin and is never passed as CLI/env/file.
try {
    $priorPreference=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    $appOutput = $key | & $docker @dc start -ai $app 2>&1
    $ErrorActionPreference=$priorPreference
    $appOutput = @($appOutput | ForEach-Object { $_.ToString().Replace($key,'[redacted]') })
    $appOutput | Set-Content -Encoding UTF8 (Join-Path $results 'app-console.log')
    $appExit=& $docker @dc wait $app
} finally {
    $key=$null
    & $docker @dc stop $gateway | Out-Null
}
$gatewayExit=& $docker @dc wait $gateway
@{app_exit=$appExit;gateway_exit=$gatewayExit} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $results 'container-exits.json')
Write-Output "App exit=$appExit; model ledger and evaluation: $results"
if ([int]$appExit -ne 0) { throw 'Decision pilot did not complete; inspect local sanitized diagnostics' }
