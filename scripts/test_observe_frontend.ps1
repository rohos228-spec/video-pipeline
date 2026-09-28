param([string]$ResultsRoot, [switch]$Build)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$root=[IO.Path]::GetFullPath($ResultsRoot)
if (Test-Path -LiteralPath $root) { throw 'Use fresh evidence directory' }
New-Item -ItemType Directory -Path "$root/source/web", "$root/docker-config", "$root/artifacts" | Out-Null
$safety='safe.directory=' + $repo.Replace('\','/')
$files=& git -c $safety -c core.quotepath=false -C $repo ls-files web scripts/bump_studio_version.py
$files=@($files | Where-Object { $_ -notmatch '^web/out/' })
$files+=@('tests/observe_v1/frontend_contract.mjs','web/src/lib/observation-view.ts','web/src/lib/observations.ts','web/src/components/orchestrator/observation-section.tsx')
$manifest=@{}
foreach($relative in ($files | Select-Object -Unique)) {
    if($relative -match '(^|/)\.env($|\.)|(^|/)node_modules/|(^|/)\.git/|\.(db|key|pem)$') { continue }
    $from=Join-Path $repo $relative
    if(-not (Test-Path -LiteralPath $from)) { continue }
    $to=Join-Path "$root/source" $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $to) -Force | Out-Null
    Copy-Item -LiteralPath $from -Destination $to
    $manifest[$relative]=(Get-FileHash $to).Hash.ToLowerInvariant()
}
$manifest | ConvertTo-Json | Set-Content "$root/source/source-manifest.json" -Encoding utf8
$docker='C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$base=@('--config',"$root/docker-config",'--host','npipe:////./pipe/dockerDesktopLinuxEngine')
$name='vp-observe-ui-build-'+[guid]::NewGuid().ToString('N').Substring(0,10)
$program='mkdir -p /results /tools/bin; ln -s /usr/local/lib/python3.12/site-packages/playwright/driver/node /tools/bin/node; export PATH=/tools/bin:$PATH HOME=/tmp/clean-home NEXT_TELEMETRY_DISABLED=1; ln -s /dependencies/node_modules /observe-source/web/node_modules; cd /observe-source/web; node /observe-source/tests/observe_v1/frontend_contract.mjs > /results/frontend-contract.txt 2>&1 || { cat /results/frontend-contract.txt; exit 1; };'
if($Build) { $program+=' node node_modules/typescript/bin/tsc --noEmit > /results/typecheck.txt 2>&1 || { cat /results/typecheck.txt; exit 2; }; node node_modules/next/dist/bin/next build > /results/build.txt 2>&1 || { cat /results/build.txt; exit 3; }; cp -r out /results/out; sha256sum out/index.html > /results/frontend-entry.sha256;' }
& $docker @base create --pull never --network none --cap-drop ALL --security-opt no-new-privileges --name $name vp-observe-frontend-deps:20260928 sh -c $program | Out-Null
& $docker @base cp "$root/source/." "${name}:/observe-source"
& $docker @base start --attach $name *> "$root/artifacts/console.txt"
$result=$LASTEXITCODE
& $docker @base cp "${name}:/results/." "$root/artifacts"
@{container=$name;network='none';test_exit=$result;image='vp-observe-frontend-deps:20260928';paid_calls=0} | ConvertTo-Json | Set-Content "$root/artifacts/config.json"
Write-Output "Frontend exit: $result; evidence: $root"
exit $result
