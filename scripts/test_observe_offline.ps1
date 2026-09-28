param(
    [string]$ResultsRoot,
    [string]$Image = 'vp-offline-diagnostic:20260924',
    [string]$Target = 'journal',
    [string]$GroupMode = 'observe'
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
$files = @($tracked) + @('tests/observe_v1/test_journal.py', 'tests/observe_v1/test_runtime.py', 'tests/observe_v1/container_test_entry.py', 'tests/observe_v1/group_acceptance.py')
$manifest = @{}
foreach ($relative in $files) {
    if ($relative -match '(^|/)\.env($|\.)|(^|/)\.git(/|$)|\.(db|sqlite|pem|key)$') { throw "Disallowed source: $relative" }
    $fromPath = Join-Path $sourceRoot $relative
    $toPath = Join-Path $stagePath $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $toPath) -Force | Out-Null
    Copy-Item -LiteralPath $fromPath -Destination $toPath
    $manifest[$relative.Replace('\', '/')] = (Get-FileHash -LiteralPath $toPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $stagePath 'source-manifest.json'), ($manifest | ConvertTo-Json -Depth 4), $utf8)
$name = 'vp-observe-test-' + (Get-Date -Format 'yyyyMMddHHmmss')
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$entryFile = 'tests/observe_v1/container_test_entry.py'
if ($Target -eq 'group') { $entryFile = 'tests/observe_v1/group_acceptance.py' }
$connectionArgs = @('--config', $configPath, '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
$dockerArgs = $connectionArgs + @(
    'create', '--pull', 'never', '--name', $name, '--network', 'none', '--cap-drop', 'ALL',
    '--security-opt', 'no-new-privileges',
    '--workdir', '/observe-source', '--env', 'PYTHONPATH=/observe-source', '--env', 'OBSERVE_SOURCE=/observe-source',
    '--env', "OBSERVE_TEST_TARGET=$Target",
    '--env', "OBSERVE_GROUP_MODE=$GroupMode",
    $Image, 'python', $entryFile)
$containerId = & $docker @dockerArgs
if ($LASTEXITCODE -ne 0) { throw 'Cannot create offline test container' }
# Copy into a fresh directory, not over the image's old application files.
# Docker cp avoids host drive sharing; no customer directory is mounted at all.
& $docker @connectionArgs cp "$stagePath/." "${name}:/observe-source"
if ($LASTEXITCODE -ne 0) { throw 'Cannot copy fresh test source' }
if ($Target -eq 'group') {
    $fixturePath = Join-Path $resultsPath 'fixtures'
    New-Item -ItemType Directory -Path $fixturePath | Out-Null
    $preparedFixtures = [IO.Path]::GetFullPath((Join-Path $sourceRoot '../diag_v2'))
    foreach ($filename in @('group_runner.py', 'free_runner.py', 'store.py')) {
        Copy-Item -LiteralPath (Join-Path $preparedFixtures $filename) -Destination (Join-Path $fixturePath $filename)
    }
    & $docker @connectionArgs cp "$fixturePath/." "${name}:/observe-fixtures"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy prepared synthetic fixture helpers' }
}
$output = & $docker @connectionArgs start --attach $name 2>&1
$testExit = $LASTEXITCODE
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
