param([switch]$Test)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-env.ps1")
$repoRoot = Split-Path $PSScriptRoot -Parent
$manifest = Join-Path $repoRoot "src-tauri\Cargo.toml"

if ($Test) {
  cargo test --manifest-path $manifest
} else {
  cargo check --manifest-path $manifest
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
