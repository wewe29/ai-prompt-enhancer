$ErrorActionPreference = "Stop"

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
  throw "Rust cargo was not found. Install the stable MSVC Rust toolchain first."
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
  throw "npm was not found. Install a supported Node.js release first."
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

if (-not $env:OPENSSL_INCLUDE_DIR -or -not (Test-Path -LiteralPath $env:OPENSSL_INCLUDE_DIR)) {
  throw "OpenSSL headers were not found. Set OPENSSL_DIR or OPENSSL_INCLUDE_DIR."
}
if (-not $env:OPENSSL_LIB_DIR -or -not (Test-Path -LiteralPath $env:OPENSSL_LIB_DIR)) {
  throw "OpenSSL libraries were not found. Set OPENSSL_DIR or OPENSSL_LIB_DIR."
}
