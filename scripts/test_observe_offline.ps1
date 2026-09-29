param(
    [string]$ResultsRoot,
    [string]$Image = 'vp-offline-diagnostic:20260924',
    [string]$Target = 'journal',
    [string]$GroupMode = 'observe',
    [string]$DenialSeed,
    [string]$ReplayResponsePath,
    [string]$ReplayArchivePath,
    [switch]$ReplayStorageFailure,
    [ValidateSet('','normal','ledger_unavailable','hung_handler')][string]$LauncherCase='',
    [switch]$AckFailure,
    [ValidateSet('','accepted')][string]$AckFailureStage = '',
    [string]$StreamCase = 'fast',
    [switch]$StreamRed,
    [switch]$Browser
)
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($LauncherCase -and $Target -ne 'decision-gateway-replay') { throw 'LauncherCase requires decision-gateway-replay' }
if (-not $ResultsRoot) {
    $ResultsRoot = Join-Path $sourceRoot ('../../outputs/observe-v1/' + (Get-Date -Format 'yyyyMMddTHHmmss'))
}
$resultsPath = [IO.Path]::GetFullPath($ResultsRoot)
if (Test-Path -LiteralPath $resultsPath) { throw 'Use a fresh results directory; existing evidence is preserved.' }
$stagePath = Join-Path $resultsPath 'source'
$configPath = Join-Path $resultsPath 'empty-docker-config'
$artifactPath = Join-Path $resultsPath 'artifacts'
New-Item -ItemType Directory -Path $stagePath, $configPath, $artifactPath | Out-Null

# Copy tracked source only. No repository metadata, dotenv, home, DB or Docker socket.
$gitSafety = 'safe.directory=' + $sourceRoot.Replace('\', '/')
$tracked = & git -c $gitSafety -c core.quotepath=false -C $sourceRoot ls-files app prompts templates pyproject.toml
if ($LASTEXITCODE -ne 0) { throw 'Cannot enumerate tracked source' }
$files = @($tracked) + @('app/services/actions_v1.py', 'tests/observe_v1/test_actions_v1_contract.py', 'tests/observe_v1/test_journal.py', 'tests/observe_v1/test_runtime.py', 'tests/observe_v1/container_test_entry.py', 'tests/observe_v1/group_acceptance.py', 'tests/observe_v1/coverage_capture.py', 'tests/observe_v1/socket_bridge.py')
$files += @('app/services/actions_recovery.py','app/services/profiles_state.py','app/services/profiles_v1.py')
$files += 'tests/observe_v1/recovery_denials.py'
$files += @('tests/observe_v1/decision_acceptance.py','tests/observe_v1/profiles_acceptance.py')
$files += 'tests/observe_v1/live_decision_acceptance.py'
if ($Target -eq 'group') { $files += @('tests/observe_v1/group_runner.py','tests/observe_v1/media_acceptance.py','config/managed-text-pilot.example.json') }
if ($Target -eq 'diagnose') { $files += 'tests/observe_v1/test_diagnosis.py' }
if ($Target -eq 'media-contract') { $files += 'tests/observe_v1/test_media_contract.py' }
if ($Target -eq 'media-jobs') { $files += 'tests/observe_v1/test_media_jobs.py' }
if ($Target -eq 'media-observation') { $files += @('tests/observe_v1/test_media_jobs.py','tests/observe_v1/test_media_observation.py') }
if ($Target -eq 'media-transport') { $files += @('tests/observe_v1/test_media_jobs.py','tests/observe_v1/test_media_transport.py') }
if ($Target -eq 'profiles') { $files += @('tests/observe_v1/test_profiles_v1.py','tests/observe_v1/test_decision_v1.py') }
if ($Target -eq 'decision-runtime') { $files += @('tests/observe_v1/test_decision_runtime.py','tests/observe_v1/test_profiles_v1.py','config/orchestrator-runtime.example.json') }
if ($Target -eq 'decision-final') { $files += @('tests/observe_v1/test_decision_runtime.py','tests/observe_v1/test_profiles_v1.py','tests/observe_v1/test_planner_boundary.py','config/orchestrator-runtime.example.json') }
if ($Target -eq 'planner-boundary') { $files += 'tests/observe_v1/test_planner_boundary.py' }
if ($Target -eq 'readonly-pilot') { $files += @('scripts/readonly_qc_pilot.py','tests/observe_v1/test_readonly_pilot.py') }
if ($Target -eq 'operator-regression') { $files += @('tests/test_gpt_operator_apply_ops.py','tests/test_apply_ops_batches.py') }
if ($Target.StartsWith('qc-noop')) { $files += 'tests/observe_v1/test_qc_noop.py' }
if ($Target -eq 'decision-readonly') { $files += @('tests/observe_v1/decision_readonly_acceptance.py',
    'scripts/diagnostics/decision_tool_cycle_gateway.py','config/decision-tool-cycle.example.json') }
if ($Target -eq 'decision-gateway-replay') { $files += @(
    'tests/observe_v1/decision_gateway_replay.py',
    'scripts/diagnostics/decision_tool_cycle_entry.py',
    'scripts/diagnostics/decision_tool_cycle_gateway.py',
    'config/decision-tool-cycle.example.json') }
if ($Target -eq 'decision-gateway-faults') { $files += @(
    'tests/observe_v1/gateway_storage_faults.py',
    'scripts/diagnostics/decision_tool_cycle_gateway.py',
    'config/decision-tool-cycle.example.json') }
if ($Target -eq 'decision-gateway-ack') { $files += @(
    'tests/observe_v1/test_decision_tool_cycle_gateway_ack.py',
    'scripts/diagnostics/decision_tool_cycle_gateway.py',
    'config/decision-tool-cycle.example.json') }
if ($Target -eq 'decision-stream') { $files += 'tests/observe_v1/decision_stream_acceptance.py' }
if ($Target.StartsWith('decision')) { $files += 'tests/observe_v1/test_decision_v1.py' }
if ($Browser) { $files += @(& git -c $gitSafety -c core.quotepath=false -C $sourceRoot ls-files web/out web/STUDIO_VERSION) }
$manifest = @{}
foreach ($relative in $files) {
    if ($relative -match '(^|/)\.env($|\.)|(^|/)\.git(/|$)|\.(db|sqlite|pem|key)$') { throw "Disallowed source: $relative" }
    $fromPath = Join-Path $sourceRoot $relative
    $toPath = Join-Path $stagePath $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $toPath) -Force | Out-Null
    Copy-Item -LiteralPath $fromPath -Destination $toPath
    $manifest[$relative.Replace('\', '/')] = (Get-FileHash -LiteralPath $toPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
if ($Target -eq 'operator-regression') {
    # Reuse only existing pure-Python test dependencies from the preflight venv.
    # No installation, network, home, venv executables or credential directories.
    $vendorRoot = [IO.Path]::GetFullPath((Join-Path $sourceRoot '../venv2/Lib/site-packages'))
    $allowedPackages = @('pytest','_pytest','pytest_asyncio','pluggy','packaging','iniconfig','pygments')
    $shimSource = Join-Path $vendorRoot 'py.py'
    $shimTarget = Join-Path $stagePath 'test-vendor/py.py'
    New-Item -ItemType Directory -Path (Split-Path -Parent $shimTarget) -Force | Out-Null
    Copy-Item -LiteralPath $shimSource -Destination $shimTarget
    $manifest['test-vendor/py.py'] = (Get-FileHash -LiteralPath $shimTarget -Algorithm SHA256).Hash.ToLowerInvariant()
    $vendorDirs = Get-ChildItem -LiteralPath $vendorRoot -Directory | Where-Object {
        ($allowedPackages -contains $_.Name) -or ($_.Name -match '^(pytest|pytest_asyncio|pluggy|packaging|iniconfig|pygments)-[0-9].*\.dist-info$')
    }
    foreach ($vendorDir in $vendorDirs) {
        foreach ($vendorFile in (Get-ChildItem -LiteralPath $vendorDir.FullName -Recurse -File)) {
            if ($vendorFile.FullName -match '__pycache__' -or $vendorFile.Extension -eq '.pyc') { continue }
            $vendorRelative = $vendorFile.FullName.Substring($vendorRoot.Length + 1)
            $vendorTarget = Join-Path $stagePath ('test-vendor/' + $vendorRelative)
            New-Item -ItemType Directory -Path (Split-Path -Parent $vendorTarget) -Force | Out-Null
            Copy-Item -LiteralPath $vendorFile.FullName -Destination $vendorTarget
            $manifest[('test-vendor/' + $vendorRelative).Replace('\','/')] = (Get-FileHash -LiteralPath $vendorTarget -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
if ($Target -eq 'readonly-pilot') {
    $pilotInputs = @{
        'pilot-context.json' = '../../outputs/read-only-qc-pilot-v1/context.json'
        'pilot_gateway.py' = '../../outputs/read-only-qc-pilot-v1/pilot_gateway.py'
        'existing_gateway.py' = 'scripts/diagnostics/gateway.py'
    }
    foreach ($name in $pilotInputs.Keys) {
        $toPath = Join-Path $stagePath $name
        Copy-Item -LiteralPath (Join-Path $sourceRoot $pilotInputs[$name]) -Destination $toPath
        $manifest[$name] = (Get-FileHash -LiteralPath $toPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
if ($Target -in @('decision-stream','decision-readonly','decision-gateway-replay','decision-gateway-faults','decision-gateway-ack')) {
    $fromGateway = Join-Path $sourceRoot 'scripts/diagnostics/gateway.py'
    if (-not (Test-Path -LiteralPath $fromGateway)) { throw 'Versioned diagnostic gateway missing' }
    $toGateway = Join-Path $stagePath 'gateway.py'
    Copy-Item -LiteralPath $fromGateway -Destination $toGateway
    $manifest['gateway.py'] = (Get-FileHash -LiteralPath $toGateway -Algorithm SHA256).Hash.ToLowerInvariant()
}
if ($Target -eq 'decision-stream') {
    if ($StreamCase -notin @('fast','slow','timeout','late_terminal')) { throw 'Unknown stream case' }
}
if ($Target -eq 'decision-readonly' -and $ReplayResponsePath) {
    if (-not (Test-Path -LiteralPath $ReplayResponsePath)) { throw 'Replay response missing' }
    Copy-Item -LiteralPath $ReplayResponsePath -Destination (Join-Path $stagePath 'replay-response.json')
    $manifest['replay-response.json'] = (Get-FileHash -LiteralPath (Join-Path $stagePath 'replay-response.json') -Algorithm SHA256).Hash.ToLowerInvariant()
}
[IO.File]::WriteAllText((Join-Path $stagePath 'source-manifest.json'), ($manifest | ConvertTo-Json -Depth 4), $utf8)
$name = 'vp-observe-test-' + (Get-Date -Format 'yyyyMMddHHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0, 8))
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$entryFile = 'tests/observe_v1/container_test_entry.py'
if ($Target -eq 'decision-readonly') { $entryFile = 'tests/observe_v1/decision_readonly_acceptance.py' }
if ($Target -eq 'decision-gateway-replay') { $entryFile = 'tests/observe_v1/decision_gateway_replay.py' }
if ($Target -eq 'decision-gateway-faults') { $entryFile = 'tests/observe_v1/gateway_storage_faults.py' }
if ($Target -eq 'decision-gateway-ack') { $entryFile = 'tests/observe_v1/test_decision_tool_cycle_gateway_ack.py' }
if ($Target -eq 'decision-stream') { $entryFile = 'tests/observe_v1/decision_stream_acceptance.py' }
if ($Target -eq 'group') { $entryFile = 'tests/observe_v1/group_acceptance.py' }
if ($Target -eq 'actions') { $entryFile = 'tests/observe_v1/container_test_entry.py' }
$connectionArgs = @('--config', $configPath, '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
$launcherMount = @()
if ($LauncherCase) { $launcherMount = @('--mount', "type=bind,src=$artifactPath,dst=/results") }
$dockerArgs = $connectionArgs + @(
    'create', '--pull', 'never', '--name', $name, '--network', 'none', '--cap-drop', 'ALL',
    '--security-opt', 'no-new-privileges',
    '--workdir', '/observe-source', '--env', 'PYTHONPATH=/observe-source', '--env', 'OBSERVE_SOURCE=/observe-source',
    '--env', "OBSERVE_TEST_TARGET=$Target",
    '--env', "OBSERVE_GROUP_MODE=$GroupMode",
    '--env', "OBSERVE_BROWSER=$Browser",
    '--env', "OBSERVE_STREAM_CASE=$StreamCase",
    '--env', "OBSERVE_STREAM_RED=$([int]$StreamRed.IsPresent)",
    '--env', "OBSERVE_REPLAY_STORAGE_FAILURE=$([int]$ReplayStorageFailure.IsPresent)",
    '--env', "OBSERVE_LAUNCHER_CASE=$LauncherCase",
    '--env', "OBSERVE_ACK_FAILURE=$([int]$AckFailure.IsPresent)",
    '--env', "OBSERVE_ACK_FAILURE_STAGE=$AckFailureStage") + $launcherMount + @($Image, 'python', $entryFile)
$containerId = & $docker @dockerArgs
if ($LASTEXITCODE -ne 0) { throw 'Cannot create offline test container' }
# Copy into a fresh directory, not over the image's old application files.
# Docker cp avoids host drive sharing; no customer directory is mounted at all.
& $docker @connectionArgs cp "$stagePath/." "${name}:/observe-source"
if ($LASTEXITCODE -ne 0) { throw 'Cannot copy fresh test source' }
if ($Target -eq 'diagnose') {
    $historyPath = Join-Path $resultsPath 'history'
    New-Item -ItemType Directory -Path $historyPath | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot '../../outputs/observe-v1/20260928-ui-group-observe/artifacts/api-history.json') -Destination (Join-Path $historyPath 'normal.json')
    Copy-Item -LiteralPath (Join-Path $sourceRoot '../../outputs/observe-v1/20260928-ui-group-malformed/artifacts/api-history.json') -Destination (Join-Path $historyPath 'malformed.json')
    & $docker @connectionArgs cp "$historyPath/." "${name}:/observe-history"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy existing synthetic histories' }
}
if ($Target -in @('decision-readonly','decision-stream','decision-gateway-replay')) {
    if (-not $DenialSeed) { throw 'Explicit immutable exhausted checkpoint required' }
    if (Get-ChildItem -LiteralPath $DenialSeed -Recurse -Force -File | Where-Object { $_.Name -match '^\.env($|\.)|\.(key|pem)$' }) { throw 'Unexpected secret file in synthetic checkpoint' }
    & $docker @connectionArgs cp "$DenialSeed/." "${name}:/observe-denial-seed"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy synthetic checkpoint' }
    Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $DenialSeed) 'checkpoint-provenance.json') -Destination (Join-Path $artifactPath 'checkpoint-provenance.json')
    & $docker @connectionArgs cp (Join-Path $artifactPath 'checkpoint-provenance.json') "${name}:/observe-checkpoint-provenance.json"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy synthetic provenance' }
}
if ($Target -eq 'decision-gateway-replay') {
    if (-not $ReplayArchivePath -or -not (Test-Path -LiteralPath $ReplayArchivePath)) { throw 'Existing run-02 archive required' }
    $replayStage = Join-Path $resultsPath 'replay-events'
    foreach ($ordinal in 1,2) {
        $fromDir = Join-Path $ReplayArchivePath "gateway-results/gateway-bodies/$ordinal"
        $toDir = Join-Path $replayStage "$ordinal"
        New-Item -ItemType Directory -Force -Path $toDir | Out-Null
        $events = @(Get-ChildItem -LiteralPath $fromDir -Filter 'events-*.json' -File)
        if (-not $events) { throw "Missing archived events for response $ordinal" }
        foreach ($eventFile in $events) { Copy-Item -LiteralPath $eventFile.FullName -Destination $toDir }
    }
    & $docker @connectionArgs cp "$replayStage/." "${name}:/observe-replay"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot stage archived sanitized SSE events' }
}
if ($Target -eq 'decision-gateway-faults') {
    if (-not $ReplayArchivePath -or -not (Test-Path -LiteralPath $ReplayArchivePath)) { throw 'Existing run-02 archive required' }
    $historicalCopy = Join-Path $resultsPath 'historical-ledger-copy'
    New-Item -ItemType Directory -Path $historicalCopy | Out-Null
    foreach ($filename in @('decision-gateway.sqlite','decision-gateway.sqlite-wal','decision-gateway.sqlite-shm')) {
        $original = Join-Path $ReplayArchivePath "gateway-results/$filename"
        if (Test-Path -LiteralPath $original) { Copy-Item -LiteralPath $original -Destination $historicalCopy }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $historicalCopy 'decision-gateway.sqlite'))) { throw 'Copied historical ledger required' }
    & $docker @connectionArgs cp "$historicalCopy/." "${name}:/historical-ledger"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot stage historical ledger copy' }
}
if ($Target -eq 'group') {
    if ($GroupMode.StartsWith('deny-') -or $GroupMode.StartsWith('decision-') -or $GroupMode.StartsWith('profiles-')) {
        if (-not $DenialSeed) { throw 'Explicit immutable synthetic -DenialSeed required for checkpoint scenarios' }
        if (-not (Test-Path -LiteralPath $DenialSeed)) { throw 'Existing exhausted synthetic checkpoint required' }
        if (Get-ChildItem -LiteralPath $DenialSeed -Recurse -Force -File | Where-Object { $_.Name -match '^\.env($|\.)|\.(key|pem)$' }) { throw 'Unexpected secret file in synthetic checkpoint' }
        & $docker @connectionArgs cp "$DenialSeed/." "${name}:/observe-denial-seed"
        if ($LASTEXITCODE -ne 0) { throw 'Cannot copy exhausted synthetic checkpoint' }
        if ($GroupMode.StartsWith('decision-managed-')) {
            $provenance=Join-Path (Split-Path -Parent $DenialSeed) 'checkpoint-provenance.json'
            & $docker @connectionArgs cp $provenance "${name}:/observe-checkpoint-provenance.json"
            if ($LASTEXITCODE -ne 0) { throw 'Synthetic checkpoint provenance required' }
        }
    }
    # Prepared HTTP answers are versioned in tests/observe_v1/group_runner.py.
}
if ($Browser) {
    & $docker @connectionArgs start $name | Out-Null
    @{container=$name;network='none';browser_port=18767;source_commit=(& git -c $gitSafety -C $sourceRoot rev-parse HEAD)} | ConvertTo-Json | Set-Content (Join-Path $artifactPath 'browser-config.json')
    Write-Output "Browser container running: $name; evidence: $artifactPath"
    exit 0
}
if ($LauncherCase) {
    & $docker @connectionArgs start $name | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot start offline launcher check' }
    $readyPath=Join-Path $artifactPath 'launcher-ready.json'
    $ready=$false
    for ($i=0;$i -lt 180;$i++) {
        if (Test-Path -LiteralPath $readyPath) { $ready=$true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        & $docker @connectionArgs logs $name 2>&1 | Set-Content -Encoding UTF8 (Join-Path $artifactPath 'test-output.txt')
        throw 'Offline Decision replay did not reach launcher drain'
    }
    & (Join-Path $sourceRoot 'scripts/run_decision_tool_cycle.ps1') -OfflineDrainCheck -ResultsRoot $resultsPath -GatewayContainer $name -OfflineDrainSeconds 5
    $drain=Get-Content -Raw (Join-Path $resultsPath 'gateway-drain.json') | ConvertFrom-Json
    $graceMarker=Test-Path -LiteralPath (Join-Path $artifactPath 'gateway-process-exited.marker')
    $passed=if ($LauncherCase -eq 'normal') {
        $drain.settled -and $drain.gateway_process_graceful -and -not $drain.forced_container_stop -and $graceMarker -and
        $drain.pending -eq 0 -and $drain.unresolved -eq 0
    } elseif ($LauncherCase -eq 'ledger_unavailable') {
        -not $drain.settled -and $drain.forced_container_stop -and $drain.pending -eq 'unknown' -and $drain.unresolved -eq 'unknown'
    } else {
        -not $drain.settled -and $drain.forced_container_stop -and $drain.pending -ge 1 -and $drain.unresolved -ge 1
    }
    & $docker @connectionArgs logs $name 2>&1 | Set-Content -Encoding UTF8 (Join-Path $artifactPath 'test-output.txt')
    @{container=$name;image=$Image;network='none';launcher_case=$LauncherCase;
      test_exit_code=$(if($passed){0}else{1});gateway_process_graceful=$graceMarker;
      source_commit=(& git -c $gitSafety -C $sourceRoot rev-parse HEAD);paid_calls=0} |
      ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $artifactPath 'run-config.json')
    Write-Output "Launcher case=$LauncherCase pass=$passed; artifacts: $artifactPath"
    if (-not $passed) { exit 1 }
    exit 0
}
$started = & $docker @connectionArgs start $name 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Cannot start offline test container' }
$waitOutput = & $docker @connectionArgs wait $name 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Cannot wait for offline test container' }
$testExit = [int]($waitOutput | Select-Object -Last 1)
$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue' # Windows PowerShell reports native stderr as non-terminating errors.
$output = & $docker @connectionArgs logs $name 2>&1
$ErrorActionPreference = $savedErrorActionPreference
& $docker @connectionArgs cp "${name}:/results/." $artifactPath
if ($LASTEXITCODE -ne 0) { throw 'Cannot collect offline test artifacts' }
[IO.File]::WriteAllText((Join-Path $artifactPath 'test-output.txt'), ($output | Out-String), $utf8)
[IO.File]::WriteAllText((Join-Path $artifactPath 'run-config.json'), (@{
    container = $name; image = $Image; network = 'none'; test_exit_code = $testExit;
    source_commit = (& git -c $gitSafety -C $sourceRoot rev-parse HEAD); source_root = $sourceRoot;
    scope = 'synthetic unittest databases only'; paid_calls = 0;
} | ConvertTo-Json), $utf8)
Write-Output "Test exit: $testExit; artifacts: $artifactPath; container preserved: $name"
exit $testExit
