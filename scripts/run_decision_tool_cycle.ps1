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
if (-not (Test-Path -LiteralPath $KeySourcePath)) { throw 'Authorized key source absent' }
$safe = 'safe.directory=' + $source.Replace('\','/')
if ((& git -c $safe -C $source status --porcelain)) { throw 'Source worktree is not clean' }
$sha = & git -c $safe -C $source rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify source version' }
$key = $null
foreach ($line in [IO.File]::ReadLines($KeySourcePath)) {
    if ($line -match '^GPT_API_KEY\s*=\s*(.+?)\s*$') {
        $candidate = $Matches[1].Trim().Trim('"').Trim("'")
        if ($candidate) { $key = $candidate }
    }
}
if (-not $key -or $key.Length -lt 12) { throw 'GPT_API_KEY unavailable; no POST' }
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
Copy-Item (Join-Path $source 'config\decision-tool-cycle.example.json') (Join-Path $gatewayStage 'pilot-config.json')
$gatewayHashes = @{}
foreach ($item in @('gateway.py','decision_tool_cycle_gateway.py','pilot-config.json')) {
    $gatewayHashes[$item] = (Get-FileHash (Join-Path $gatewayStage $item) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$gatewayHashes | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $results 'gateway-source-hashes.json')
$suffix = [guid]::NewGuid().ToString('N').Substring(0,12)
$app = 'vp-decision-online-app-' + $suffix
$gateway = 'vp-decision-online-gateway-' + $suffix
$volume = 'vp-decision-online-socket-' + $suffix
$dc = @('--config',$dockerConfig,'--host','npipe:////./pipe/dockerDesktopLinuxEngine')
& $docker @dc info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Engine unavailable; no POST' }
& $docker @dc volume create $volume | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated socket volume' }
$image='vp-offline-diagnostic:20260924'
& $docker @dc create --pull never --name $gateway --network bridge --cap-drop ALL --security-opt no-new-privileges --mount "type=volume,src=$volume,dst=/gateway" --mount "type=bind,src=$gatewayResults,dst=/results" $image python /gateway-source/decision_tool_cycle_gateway.py | Out-Null
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
  app_network='none';gateway_network='bridge';key_in_files=$false;key_in_env=$false} |
  ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $results 'run-metadata.json')
& $docker @dc start $gateway | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Gateway start failed; no POST' }
$ready=$false
for ($i=0;$i -lt 30;$i++) {
    & $docker @dc exec $gateway test -S /gateway/llm.sock 2>$null
    if ($LASTEXITCODE -eq 0) { $ready=$true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw 'Gateway socket unavailable; no POST' }
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
