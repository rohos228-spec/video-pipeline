param(
    [string]$ResultsRoot,
    [string]$Image = 'vp-offline-diagnostic:20260924',
    [string]$Target = 'journal',
    [string]$GroupMode = 'observe',
    [string]$DenialSeed,
    [string]$StreamCase = 'fast',
    [switch]$StreamRed,
    [switch]$Browser
)
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
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
if ($Target -eq 'diagnose') { $files += 'tests/observe_v1/test_diagnosis.py' }
if ($Target -eq 'profiles') { $files += @('tests/observe_v1/test_profiles_v1.py','tests/observe_v1/test_decision_v1.py') }
if ($Target -eq 'decision-runtime') { $files += @('tests/observe_v1/test_decision_runtime.py','tests/observe_v1/test_profiles_v1.py','config/orchestrator-runtime.example.json') }
if ($Target -eq 'decision-final') { $files += @('tests/observe_v1/test_decision_runtime.py','tests/observe_v1/test_profiles_v1.py','tests/observe_v1/test_planner_boundary.py','config/orchestrator-runtime.example.json') }
if ($Target -eq 'planner-boundary') { $files += 'tests/observe_v1/test_planner_boundary.py' }
if ($Target -eq 'readonly-pilot') { $files += @('scripts/readonly_qc_pilot.py','tests/observe_v1/test_readonly_pilot.py') }
if ($Target -eq 'operator-regression') { $files += @('tests/test_gpt_operator_apply_ops.py','tests/test_apply_ops_batches.py') }
if ($Target.StartsWith('qc-noop')) { $files += 'tests/observe_v1/test_qc_noop.py' }
if ($Target -eq 'decision-readonly') { $files += 'tests/observe_v1/decision_readonly_acceptance.py' }
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
        'existing_gateway.py' = '../diag_v2/gateway.py'
    }
    foreach ($name in $pilotInputs.Keys) {
        $toPath = Join-Path $stagePath $name
        Copy-Item -LiteralPath (Join-Path $sourceRoot $pilotInputs[$name]) -Destination $toPath
        $manifest[$name] = (Get-FileHash -LiteralPath $toPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
if ($Target -eq 'decision-stream') {
    if ($StreamCase -notin @('fast','slow','timeout','late_terminal')) { throw 'Unknown stream case' }
    $fromGateway = [IO.Path]::GetFullPath((Join-Path $sourceRoot '../diag_v2/gateway.py'))
    $toGateway = Join-Path $stagePath 'gateway.py'
    Copy-Item -LiteralPath $fromGateway -Destination $toGateway
    $manifest['gateway.py'] = (Get-FileHash -LiteralPath $toGateway -Algorithm SHA256).Hash.ToLowerInvariant()
}
[IO.File]::WriteAllText((Join-Path $stagePath 'source-manifest.json'), ($manifest | ConvertTo-Json -Depth 4), $utf8)
$name = 'vp-observe-test-' + (Get-Date -Format 'yyyyMMddHHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0, 8))
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$entryFile = 'tests/observe_v1/container_test_entry.py'
if ($Target -eq 'decision-readonly') { $entryFile = 'tests/observe_v1/decision_readonly_acceptance.py' }
if ($Target -eq 'decision-stream') { $entryFile = 'tests/observe_v1/decision_stream_acceptance.py' }
if ($Target -eq 'group') { $entryFile = 'tests/observe_v1/group_acceptance.py' }
if ($Target -eq 'actions') { $entryFile = 'tests/observe_v1/container_test_entry.py' }
$connectionArgs = @('--config', $configPath, '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
$dockerArgs = $connectionArgs + @(
    'create', '--pull', 'never', '--name', $name, '--network', 'none', '--cap-drop', 'ALL',
    '--security-opt', 'no-new-privileges',
    '--workdir', '/observe-source', '--env', 'PYTHONPATH=/observe-source', '--env', 'OBSERVE_SOURCE=/observe-source',
    '--env', "OBSERVE_TEST_TARGET=$Target",
    '--env', "OBSERVE_GROUP_MODE=$GroupMode",
    '--env', "OBSERVE_BROWSER=$Browser",
    '--env', "OBSERVE_STREAM_CASE=$StreamCase",
    '--env', "OBSERVE_STREAM_RED=$([int]$StreamRed.IsPresent)",
    $Image, 'python', $entryFile)
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
if ($Target -in @('decision-readonly','decision-stream')) {
    if (-not $DenialSeed) { throw 'Explicit immutable exhausted checkpoint required' }
    if (Get-ChildItem -LiteralPath $DenialSeed -Recurse -Force -File | Where-Object { $_.Name -match '^\.env($|\.)|\.(key|pem)$' }) { throw 'Unexpected secret file in synthetic checkpoint' }
    & $docker @connectionArgs cp "$DenialSeed/." "${name}:/observe-denial-seed"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy synthetic checkpoint' }
    Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $DenialSeed) 'checkpoint-provenance.json') -Destination (Join-Path $artifactPath 'checkpoint-provenance.json')
    & $docker @connectionArgs cp (Join-Path $artifactPath 'checkpoint-provenance.json') "${name}:/observe-checkpoint-provenance.json"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy synthetic provenance' }
}
if ($Target -eq 'group') {
    if ($GroupMode.StartsWith('deny-') -or $GroupMode.StartsWith('decision-') -or $GroupMode.StartsWith('profiles-')) {
        if (-not $DenialSeed) { $DenialSeed = Join-Path $sourceRoot '../../outputs/actions-v1-final/recovery-a-02/artifacts/free-run-data' }
        if (-not (Test-Path -LiteralPath $DenialSeed)) { throw 'Existing exhausted synthetic checkpoint required' }
        if (Get-ChildItem -LiteralPath $DenialSeed -Recurse -Force -File | Where-Object { $_.Name -match '^\.env($|\.)|\.(key|pem)$' }) { throw 'Unexpected secret file in synthetic checkpoint' }
        & $docker @connectionArgs cp "$DenialSeed/." "${name}:/observe-denial-seed"
        if ($LASTEXITCODE -ne 0) { throw 'Cannot copy exhausted synthetic checkpoint' }
    }
    $fixturePath = Join-Path $resultsPath 'fixtures'
    New-Item -ItemType Directory -Path $fixturePath | Out-Null
    $preparedFixtures = [IO.Path]::GetFullPath((Join-Path $sourceRoot '../diag_v2'))
    foreach ($filename in @('group_runner.py', 'free_runner.py', 'store.py')) {
        Copy-Item -LiteralPath (Join-Path $preparedFixtures $filename) -Destination (Join-Path $fixturePath $filename)
    }
    & $docker @connectionArgs cp "$fixturePath/." "${name}:/observe-fixtures"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy prepared synthetic fixture helpers' }
}
if ($Browser) {
    & $docker @connectionArgs start $name | Out-Null
    @{container=$name;network='none';browser_port=18767;source_commit=(& git -c $gitSafety -C $sourceRoot rev-parse HEAD)} | ConvertTo-Json | Set-Content (Join-Path $artifactPath 'browser-config.json')
    Write-Output "Browser container running: $name; evidence: $artifactPath"
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
