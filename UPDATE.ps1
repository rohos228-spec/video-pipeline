$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Repo
& (Join-Path $Repo "FORCE-UPDATE.cmd")
