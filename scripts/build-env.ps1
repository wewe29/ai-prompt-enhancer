$ErrorActionPreference = "Stop"

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
  throw "ENVIRONMENT: Rust cargo was not found. Install the stable MSVC Rust toolchain first."
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
  throw "ENVIRONMENT: npm was not found. Install a supported Node.js release first."
}

# 构建与测试的临时目录统一使用扁平的 %TEMP%\PromptCraft-* 前缀：
#   PromptCraft-vite-<pid>     Vite/vitest 缓存，进程退出即清理
#   PromptCraft-pytest-<pid>   pytest basetemp，会话结束即清理
#   PromptCraft-cargo-target   Rust 增量编译缓存，有意持久保留
# Cargo target 不带 pid、不随测试删除：每次清空都会让 fmt/test/clippy
# 全量重编译（Windows 冷构建约 5-15 分钟）。需要释放空间时运行
# scripts/build-check.ps1 -Clean 手动清空全部 PromptCraft-* 目录。
# CI 或需要默认 target 目录的场景可设置 PROMPTCRAFT_NO_CARGO_REDIRECT=1。
if (-not $env:CARGO_TARGET_DIR) {
  if ($env:PROMPTCRAFT_NO_CARGO_REDIRECT -ne "1") {
    $env:CARGO_TARGET_DIR = Join-Path $env:TEMP "PromptCraft-cargo-target"
  }
}

function Resolve-OpenSslEnv {
  # 返回 $null 表示未找到；否则返回包含 Dir/IncludeDir/LibDir/Probed 的哈希表。
  # 命中判定：include\openssl\ssl.h 存在（比"目录存在"可靠）。
  $probeRoots = New-Object System.Collections.Generic.List[string]
  if ($env:OPENSSL_DIR) { $probeRoots.Add($env:OPENSSL_DIR) }
  $probeRoots.Add((Join-Path $env:ProgramFiles "OpenSSL-Win64"))
  if (${env:ProgramFiles(x86)}) {
    $probeRoots.Add((Join-Path ${env:ProgramFiles(x86)} "OpenSSL-Win64"))
  }
  $probeRoots.Add((Join-Path $env:ProgramFiles "OpenSSL"))
  if ($env:LOCALAPPDATA) {
    $probeRoots.Add((Join-Path $env:LOCALAPPDATA "Programs\OpenSSL-Win64"))
  }

  $probed = @()
  $found = $null
  $seen = @{}
  foreach ($root in $probeRoots) {
    if (-not $root -or $seen.ContainsKey($root.ToLower())) { continue }
    $seen[$root.ToLower()] = $true
    # 注意：Join-Path 在盘符不存在时会抛 DriveNotFoundException（PS 5.1 实测），
    # 探测路径必须用纯字符串拼接，不能碰文件系统语义。
    $marker = [IO.Path]::Combine($root, "include", "openssl", "ssl.h")
    # Test-Path 在盘符不存在时会抛错（ErrorActionPreference=Stop 下是终止错误），
    # 统一按"不存在"处理，保证探测清单完整打印而不是中途夭折。
    $markerExists = $false
    $rootExists = $false
    try { $markerExists = Test-Path -LiteralPath $marker } catch { $markerExists = $false }
    try { $rootExists = Test-Path -LiteralPath $root } catch { $rootExists = $false }
    if ($markerExists) {
      $probed += "$root  =>  OK"
      $found = $root
      break
    } elseif ($rootExists) {
      $probed += "$root  =>  目录存在，但缺少 include\openssl\ssl.h（可能只装了运行库，未装开发文件）"
    } else {
      $probed += "$root  =>  不存在"
    }
  }

  if (-not $found) {
    return @{ Probed = $probed }
  }

  $includeDir = Join-Path $found "include"
  # 库目录候选：OpenSSL-Win64 的 MSVC 静态库在 lib\VC\x64\MD 下，
  # 而 libsqlite3-sys 的 build.rs 回落路径用的是 $OPENSSL_DIR/lib ——
  # 有的安装里 lib\ 存在但**是空的**（只装了头文件和运行库），那就必然链接失败。
  $libCandidates = @(
    (Join-Path $found "lib\VC\x64\MD"),
    (Join-Path $found "lib\x64"),
    (Join-Path $found "lib64"),
    (Join-Path $found "lib")
  )
  # 命中判定：**目录里有真正的 libcrypto.lib 才算命中**。
  # 原实现只 Test-Path 目录本身，于是一个"存在但空"的 lib\ 会被判为命中，
  # 探测全部通过、真正链接时才炸 LNK1181 —— 正是本项目一直在消灭的
  # "第 1 类检查（存在）通过、第 2 类检查（真能用）失败"。
  $libDir = $null
  foreach ($candidate in $libCandidates) {
    $cryptoLib = [IO.Path]::Combine($candidate, "libcrypto.lib")
    $hasLib = $false
    try { $hasLib = Test-Path -LiteralPath $cryptoLib } catch { $hasLib = $false }
    if ($hasLib) {
      $libDir = $candidate
      $probed += "$candidate  =>  OK（含 libcrypto.lib）"
      break
    }
    $dirExists = $false
    try { $dirExists = Test-Path -LiteralPath $candidate } catch { $dirExists = $false }
    if ($dirExists) {
      $probed += "$candidate  =>  目录存在但缺 libcrypto.lib（只有运行库，没有开发库）"
    }
  }
  # $libDir 保持 $null 时，调用方的 OPENSSL_LIB_DIR 校验会带着探测清单报缺库，
  # 而不是把错误推迟到链接阶段。
  return @{ Dir = $found; IncludeDir = $includeDir; LibDir = $libDir; Probed = $probed }
}

function Show-OpenSslMissingHelp {
  param([string]$Kind)
  Write-Host ""
  Write-Host "[ENVIRONMENT] $Kind。已按顺序探测以下位置：" -ForegroundColor Yellow
  $openSsl.Probed | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
  Write-Host ""
  Write-Host "解决方法（任选其一）：" -ForegroundColor Yellow
  Write-Host "  1. 安装 64 位 OpenSSL 开发文件并重启终端："
  Write-Host '       winget install ShiningLight.OpenSSL'
  Write-Host '       setx OPENSSL_DIR "C:\Program Files\OpenSSL-Win64"'
  Write-Host "     然后重新打开终端使环境变量生效，再重试。"
  Write-Host "  2. 已有自定义安装？指向其安装目录即可："
  Write-Host '       setx OPENSSL_DIR "<你的 OpenSSL 安装目录>"'
  Write-Host "  3. 详细说明见 README.md 的「OpenSSL 依赖」小节；CI 上使用 vcpkg 的 openssl:x64-windows。"
}

$openSsl = Resolve-OpenSslEnv
# 显式设置的 OPENSSL_DIR 必须真实可用：该目录缺少开发文件（include\openssl\ssl.h）
# 且未显式给出 OPENSSL_INCLUDE_DIR 时，直接按环境错误失败——不静默改用其他
# 探测路径，否则 DIR 会与 INCLUDE/LIB 指向不一致（cargo 能编过，到
# build-portable 复制 DLL 时才失败，更难排查）。
if ($env:OPENSSL_DIR -and -not $env:OPENSSL_INCLUDE_DIR) {
  $explicitMarker = [IO.Path]::Combine($env:OPENSSL_DIR, "include", "openssl", "ssl.h")
  $explicitMarkerExists = $false
  try { $explicitMarkerExists = Test-Path -LiteralPath $explicitMarker } catch { $explicitMarkerExists = $false }
  if (-not $explicitMarkerExists) {
    Show-OpenSslMissingHelp "OPENSSL_DIR 指向的目录缺少 include\openssl\ssl.h"
    throw "OPENSSL_ENV_MISSING: OPENSSL_DIR was set but contains no OpenSSL development files."
  }
}

if ($openSsl.Dir) {
  # 显式设置的 OPENSSL_INCLUDE_DIR / OPENSSL_LIB_DIR（如 CI 的 vcpkg）优先于探测结果。
  if (-not $env:OPENSSL_DIR) { $env:OPENSSL_DIR = $openSsl.Dir }
  if (-not $env:OPENSSL_INCLUDE_DIR) { $env:OPENSSL_INCLUDE_DIR = $openSsl.IncludeDir }
  if (-not $env:OPENSSL_LIB_DIR -and $openSsl.LibDir) { $env:OPENSSL_LIB_DIR = $openSsl.LibDir }
  $openSslBin = Join-Path $openSsl.Dir "bin"
  if (Test-Path -LiteralPath $openSslBin) {
    $env:PATH = "$openSslBin;$env:PATH"
  }
}

Write-Host "OpenSSL 探测: INCLUDE=$($env:OPENSSL_INCLUDE_DIR), LIB=$($env:OPENSSL_LIB_DIR), DIR=$($env:OPENSSL_DIR)"

if (-not $env:OPENSSL_INCLUDE_DIR -or -not (Test-Path -LiteralPath $env:OPENSSL_INCLUDE_DIR)) {
  Show-OpenSslMissingHelp "未找到 OpenSSL 头文件"
  throw "OPENSSL_ENV_MISSING: OpenSSL headers were not found. Set OPENSSL_DIR or OPENSSL_INCLUDE_DIR."
}
if (-not $env:OPENSSL_LIB_DIR -or -not (Test-Path -LiteralPath $env:OPENSSL_LIB_DIR)) {
  Show-OpenSslMissingHelp "未找到 OpenSSL 库文件"
  throw "OPENSSL_ENV_MISSING: OpenSSL libraries were not found. Set OPENSSL_DIR or OPENSSL_LIB_DIR."
}
