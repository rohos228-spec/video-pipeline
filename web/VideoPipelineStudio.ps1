# Запуск из web\ — переход в корень репозитория
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root "installer\VideoPipelineLauncher.ps1")
