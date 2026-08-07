$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-env.ps1")

$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot
$package = Get-Content -LiteralPath (Join-Path $repoRoot "package.json") -Raw | ConvertFrom-Json
$version = $package.version

npm.cmd run tauri -- build --no-bundle
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$releaseRoot = Join-Path $repoRoot "release"
$portableDir = Join-Path $releaseRoot "PromptCraft-$version-windows-x64-portable"
New-Item -ItemType Directory -Path $portableDir -Force | Out-Null

Copy-Item -LiteralPath (Join-Path $repoRoot "src-tauri\target\release\prompt-craft.exe") -Destination (Join-Path $portableDir "PromptCraft.exe") -Force

$openSslBin = Join-Path $env:OPENSSL_DIR "bin"
Get-ChildItem -LiteralPath $openSslBin -File | Where-Object { $_.Name -match '^lib(crypto|ssl)-.*-x64\.dll$' } | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $portableDir -Force
}

$releaseReadme = Join-Path $repoRoot "release-assets\README.txt"
if (Test-Path -LiteralPath $releaseReadme) {
  Copy-Item -LiteralPath $releaseReadme -Destination $portableDir -Force
}
$guide = Get-ChildItem -LiteralPath $repoRoot -File | Where-Object { $_.Name -like 'PromptCraft*.md' } | Select-Object -First 1
if ($guide) {
  Copy-Item -LiteralPath $guide.FullName -Destination $portableDir -Force
}
$webViewBootstrapper = Join-Path $repoRoot "release-assets\MicrosoftEdgeWebView2Setup.exe"
if (Test-Path -LiteralPath $webViewBootstrapper) {
  Copy-Item -LiteralPath $webViewBootstrapper -Destination $portableDir -Force
}

$archivePath = Join-Path $releaseRoot "PromptCraft-$version-windows-x64-portable.zip"
Compress-Archive -Path (Join-Path $portableDir "*") -DestinationPath $archivePath -Force
Write-Host "Portable package: $archivePath"
