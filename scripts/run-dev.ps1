$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-env.ps1")
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot
npm.cmd run tauri -- dev
exit $LASTEXITCODE
