param([string]$ResultsRoot)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $ResultsRoot) { throw 'A fresh results directory is required' }
$root = [IO.Path]::GetFullPath($ResultsRoot)
if (Test-Path -LiteralPath $root) { throw 'Preserve existing evidence; use a new directory' }
New-Item -ItemType Directory -Path "$root/manifests", "$root/docker-config" | Out-Null
Copy-Item -LiteralPath "$repo/web/package.json", "$repo/web/package-lock.json" -Destination "$root/manifests"
$lock = Get-Content -LiteralPath "$root/manifests/package-lock.json" -Raw
$locked = $lock | ConvertFrom-Json -AsHashtable
foreach ($item in $locked.packages.Values) {
    if ($item.resolved -and $item.resolved -notmatch '^https://registry\.npmjs\.org/') { throw 'Review nonstandard dependency source before download' }
}
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$argsBase = @('--config', "$root/docker-config", '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
$name = 'vp-observe-deps-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$program = 'mkdir -p /tools/bin /tmp/clean-home; ln -s /usr/local/lib/python3.12/site-packages/playwright/driver/node /tools/bin/node; export PATH=/tools/bin:$PATH HOME=/tmp/clean-home XDG_CONFIG_HOME=/tmp/clean-home npm_config_userconfig=/tmp/empty-npmrc npm_config_globalconfig=/tmp/empty-global-npmrc npm_config_registry=https://registry.npmjs.org PNPM_HOME=/tmp/pnpm; touch /tmp/empty-npmrc /tmp/empty-global-npmrc; node /portable-npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org'
& $docker @argsBase create --pull never --name $name --cap-drop ALL --security-opt no-new-privileges --workdir /dependencies vp-offline-diagnostic:20260924 sh -c $program
if ($LASTEXITCODE) { throw 'Dependency container creation failed' }
& $docker @argsBase cp "$root/manifests/." "${name}:/dependencies"
& $docker @argsBase cp 'C:/Program Files/nodejs/node_modules/npm' "${name}:/portable-npm"
if ($LASTEXITCODE) { throw 'Portable package manager copy failed' }
& $docker @argsBase start --attach $name *> "$root/dependency-install.txt"
$result = $LASTEXITCODE
if ($result) { throw "Dependency install failed: $result; see local log" }
# Save the dependency-only layer. All builds/tests run in network-none containers.
& $docker @argsBase commit $name vp-observe-frontend-deps:20260928 | Set-Content "$root/image-id.txt"
& $docker @argsBase cp "${name}:/dependencies/package-lock.json" "$root/installed-lock.yaml"
if ((Get-FileHash "$root/installed-lock.yaml").Hash -ne (Get-FileHash "$repo/web/package-lock.json").Hash) { throw 'Lockfile changed' }
@{container=$name; install_scripts=$false; registry='https://registry.npmjs.org'; manifests_only=$true; lock_sha256=(Get-FileHash "$repo/web/package-lock.json").Hash; paid_calls=0} | ConvertTo-Json | Set-Content "$root/config.json"
Write-Output "Dependencies prepared; evidence: $root"
