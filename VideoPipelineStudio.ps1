# Точка входа из корня репозитория (то же, что VideoPipelineStudio.cmd)
$Root = $PSScriptRoot
& (Join-Path $Root "installer\VideoPipelineLauncher.ps1")
