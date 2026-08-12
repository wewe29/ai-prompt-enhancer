$ErrorActionPreference = "Stop"

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
  throw "Rust cargo was not found. Install the stable MSVC Rust toolchain first."
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
  throw "npm was not found. Install a supported Node.js release first."
}

if (-not $env:CARGO_TARGET_DIR) {
  $env:CARGO_TARGET_DIR = Join-Path $env:TEMP "promptcraft-cargo-target"
}

if (-not $env:OPENSSL_DIR) {
  $defaultOpenSsl = Join-Path $env:ProgramFiles "OpenSSL-Win64"
  if (Test-Path -LiteralPath $defaultOpenSsl) {
    $env:OPENSSL_DIR = $defaultOpenSsl
  }
}

if ($env:OPENSSL_DIR) {
  if (-not $env:OPENSSL_INCLUDE_DIR) {
    $env:OPENSSL_INCLUDE_DIR = Join-Path $env:OPENSSL_DIR "include"
  }
  if (-not $env:OPENSSL_LIB_DIR) {
    $libCandidates = @(
      (Join-Path $env:OPENSSL_DIR "lib\VC\x64\MD"),
      (Join-Path $env:OPENSSL_DIR "lib")
    )
    $env:OPENSSL_LIB_DIR = $libCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  }
  $openSslBin = Join-Path $env:OPENSSL_DIR "bin"
  if (Test-Path -LiteralPath $openSslBin) {
    $env:PATH = "$openSslBin;$env:PATH"
  }
}

Write-Host "OpenSSL 探测: INCLUDE=$($env:OPENSSL_INCLUDE_DIR), LIB=$($env:OPENSSL_LIB_DIR), DIR=$($env:OPENSSL_DIR)"

if (-not $env:OPENSSL_INCLUDE_DIR -or -not (Test-Path -LiteralPath $env:OPENSSL_INCLUDE_DIR)) {
  Write-Host ""
  Write-Host "未找到 OpenSSL 头文件。请安装 OpenSSL 并设置环境变量，例如："
  Write-Host '  winget install ShiningLight.OpenSSL'
  Write-Host '  setx OPENSSL_DIR "C:\Program Files\OpenSSL-Win64"'
  Write-Host "  然后重新打开终端使环境变量生效，再重试。"
  throw "OpenSSL headers were not found. Set OPENSSL_DIR or OPENSSL_INCLUDE_DIR."
}
if (-not $env:OPENSSL_LIB_DIR -or -not (Test-Path -LiteralPath $env:OPENSSL_LIB_DIR)) {
  Write-Host ""
  Write-Host "未找到 OpenSSL 库。请安装 OpenSSL 并设置环境变量，例如："
  Write-Host '  winget install ShiningLight.OpenSSL'
  Write-Host '  setx OPENSSL_DIR "C:\Program Files\OpenSSL-Win64"'
  Write-Host "  然后重新打开终端使环境变量生效，再重试。"
  throw "OpenSSL libraries were not found. Set OPENSSL_DIR or OPENSSL_LIB_DIR."
}
