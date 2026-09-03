$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-env.ps1")

$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot

# ---- 版本一致性：四处版本号必须完全一致，否则中止 ----
function Get-DeclaredVersions {
  $versions = @{}
  $package = Get-Content -LiteralPath (Join-Path $repoRoot "package.json") -Raw | ConvertFrom-Json
  $versions["package.json"] = $package.version

  $tauri = Get-Content -LiteralPath (Join-Path $repoRoot "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
  $versions["src-tauri\tauri.conf.json"] = $tauri.version

  $cargoText = Get-Content -LiteralPath (Join-Path $repoRoot "src-tauri\Cargo.toml") -Raw
  if ($cargoText -match '(?m)^\s*version\s*=\s*"([^"]+)"') {
    $versions["src-tauri\Cargo.toml"] = $Matches[1]
  } else {
    $versions["src-tauri\Cargo.toml"] = "<未找到 version 字段>"
  }

  $lockText = Get-Content -LiteralPath (Join-Path $repoRoot "src-tauri\Cargo.lock") -Raw
  if ($lockText -match '(?ms)name = "prompt-craft"\s*version = "([^"]+)"') {
    $versions["src-tauri\Cargo.lock"] = $Matches[1]
  } else {
    $versions["src-tauri\Cargo.lock"] = "<未找到 prompt-craft 条目>"
  }
  return $versions
}

$versions = Get-DeclaredVersions
$distinct = @($versions.Values | Sort-Object -Unique)
if ($distinct.Count -ne 1) {
  Write-Host "[portable] 版本不一致，请先同步以下版本号：" -ForegroundColor Red
  $versions.GetEnumerator() | Sort-Object Name | ForEach-Object {
    Write-Host "  $($_.Key) = $($_.Value)" -ForegroundColor Red
  }
  throw "Version mismatch across package.json / Cargo.toml / tauri.conf.json / Cargo.lock"
}
$version = $versions["package.json"]
Write-Host "[portable] version = $version"

npm.cmd run tauri -- build --no-bundle
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# build-env.ps1 可能重定向 CARGO_TARGET_DIR（默认 %TEMP%\PromptCraft-cargo-target）；
# 设置了 PROMPTCRAFT_NO_CARGO_REDIRECT=1 时跟随默认的 src-tauri\target。
$cargoTarget = $env:CARGO_TARGET_DIR
if (-not $cargoTarget) { $cargoTarget = Join-Path $repoRoot "src-tauri\target" }
$promptCraftExe = Join-Path $cargoTarget "release\prompt-craft.exe"

# exe 元数据校验：Tauri 会把版本写进 exe 资源；不一致说明构建产物是旧的。
# 兼容 "0.3.1" 与 "0.3.1.0" 两种形式；exe 无版本信息时仅警告。
$exeVersion = (Get-Item -LiteralPath $promptCraftExe).VersionInfo.ProductVersion
if ($exeVersion) {
  $matchesVersion = ($exeVersion -eq $version) -or $exeVersion.StartsWith("$version.")
  if (-not $matchesVersion) {
    throw "[portable] exe 版本($exeVersion)与 $version 不一致：构建产物可能是旧的，请清理构建缓存后重新构建。"
  }
  Write-Host "[portable] exe ProductVersion = $exeVersion"
} else {
  Write-Host "[portable] 警告：exe 未携带版本信息（ProductVersion 为空），跳过元数据校验。" -ForegroundColor Yellow
}

$releaseRoot = Join-Path $repoRoot "release"
$portableDir = Join-Path $releaseRoot "PromptCraft-$version-windows-x64-portable"

# 重建前清空旧目录，避免上次构建残留文件混入。
if (Test-Path -LiteralPath $portableDir) {
  Remove-Item -LiteralPath $portableDir -Recurse -Force
}
New-Item -ItemType Directory -Path $portableDir -Force | Out-Null

Copy-Item -LiteralPath $promptCraftExe -Destination (Join-Path $portableDir "PromptCraft.exe") -Force

if (-not $env:OPENSSL_DIR) {
  throw "ENVIRONMENT: OPENSSL_DIR 未设置，无法打包 OpenSSL 运行库 DLL。"
}
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

# SHA-256：格式对齐 sha256sum（<hash>  <文件名>，两空格），与既有 release-assets/SHA256-zip.txt 一致。
$hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLower()
$shaLine = "$hash  $(Split-Path $archivePath -Leaf)"
$shaPath = Join-Path $repoRoot "release-assets\SHA256-zip.txt"
[IO.File]::WriteAllText($shaPath, "$shaLine`r`n", (New-Object Text.UTF8Encoding($false)))
Write-Host "SHA-256: $shaLine"
Write-Host "已写入 $shaPath"
Write-Host "Portable package: $archivePath"
