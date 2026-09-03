param(
  [switch]$Test,
  [switch]$Clean
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$manifest = Join-Path $repoRoot "src-tauri\Cargo.toml"
# npm 阶段依赖工作目录：不依赖调用方的 cwd，统一切到仓库根。
Set-Location $repoRoot

# 退出码约定：0 = 全部通过；1 = 代码错误（CODE）；2 = 环境错误（ENVIRONMENT）。
# 注意：不要为 native 命令追加 2>&1 —— Windows PowerShell 5.1 在
# $ErrorActionPreference="Stop" 下会把 stderr 重定向变成 NativeCommandError，
# 使 cargo/clippy 的正常诊断输出被误判为异常。

if ($Clean) {
  # 清空 %TEMP% 下全部 PromptCraft-* 临时目录（含持久保留的 cargo 编译缓存）。
  $targets = @(Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter "PromptCraft-*" -ErrorAction SilentlyContinue)
  if ($targets.Count -eq 0) {
    Write-Host "%TEMP% 下没有 PromptCraft-* 临时目录，无需清理。"
    exit 0
  }
  foreach ($t in $targets) {
    try {
      Remove-Item -LiteralPath $t.FullName -Recurse -Force -ErrorAction Stop
      Write-Host "已删除 $($t.FullName)"
    } catch {
      Write-Host "无法删除 $($t.FullName)（可能被占用）：$($_.Exception.Message)" -ForegroundColor Yellow
    }
  }
  exit 0
}

# 启动时清扫陈旧临时目录（尽力而为，不作为失败项）：
# vite/pytest 目录按 pid 命名、正常应在进程结束时删除，>7 天视为残留；
# cargo target 持久保留以保增量编译，>30 天才清。
$now = Get-Date
foreach ($rule in @(
    @{ Filter = "PromptCraft-vite-*";       Days = 7 },
    @{ Filter = "PromptCraft-pytest-*";     Days = 7 },
    @{ Filter = "PromptCraft-cargo-target"; Days = 30 })) {
  Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter $rule.Filter -ErrorAction SilentlyContinue |
    Where-Object { ($now - $_.LastWriteTime).TotalDays -gt $rule.Days } |
    ForEach-Object {
      Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
      Write-Host "[verify] 已清扫陈旧临时目录 $($_.Name)（>$($rule.Days) 天）"
    }
}

# ---- 环境预检：任何失败都归类 ENVIRONMENT，退出码 2 ----
Write-Host "[verify] preflight: environment"
try {
  . (Join-Path $PSScriptRoot "build-env.ps1")
} catch {
  Write-Host "[verify] FAILED (ENVIRONMENT) at preflight: $($_.Exception.Message)" -ForegroundColor Red
  exit 2
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "[verify] FAILED (ENVIRONMENT) at preflight: 未找到 git。请安装 Git for Windows 后重试。" -ForegroundColor Red
  exit 2
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "node_modules"))) {
  Write-Host "[verify] FAILED (ENVIRONMENT) at preflight: 缺少 node_modules。请先运行 npm.cmd ci。" -ForegroundColor Red
  exit 2
}

$venvPython = Join-Path $repoRoot "evaluation\.venv\Scripts\python.exe"
$hasVenvPython = Test-Path -LiteralPath $venvPython
if (-not $hasVenvPython) {
  Write-Host "[verify] 评测 venv（evaluation\.venv）不存在，将回落系统 python。重建指引：" -ForegroundColor Yellow
  Write-Host "  python -m venv evaluation\.venv"
  Write-Host "  evaluation\.venv\Scripts\python.exe -m pip install -r evaluation\requirements.txt"
  if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[verify] FAILED (ENVIRONMENT) at preflight: 系统也找不到 python。请安装 Python 3.12 或重建 venv。" -ForegroundColor Red
    exit 2
  }
}
Write-Host ""

if ($Test) {
  # 兼容旧行为：build-check.cmd test 之外，直接 scripts\build-check.ps1 -Test 只跑 cargo test。
  cargo test --manifest-path $manifest
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  exit 0
}

$script:results = New-Object System.Collections.Generic.List[object]

function Invoke-Stage {
  param([int]$Index, [string]$Name, [scriptblock]$Body)
  Write-Host "[verify] stage $Index/7: $Name"
  $watch = [System.Diagnostics.Stopwatch]::StartNew()
  $failed = $false
  try {
    & $Body
    if ($LASTEXITCODE -ne 0) { $failed = $true }
  } catch {
    $failed = $true
    Write-Host "  error: $($_.Exception.Message)"
  }
  $watch.Stop()
  $seconds = [math]::Round($watch.Elapsed.TotalSeconds, 1)
  $script:results.Add([pscustomobject]@{ Index = $Index; Name = $Name; Ok = -not $failed; Seconds = $seconds })
  if ($failed) {
    Write-Host "[verify] FAILED (CODE) at stage ${Index}/7: $Name ($seconds s)" -ForegroundColor Red
    return $false
  }
  Write-Host "[verify] stage ${Index}/7 OK ($seconds s)"
  Write-Host ""
  return $true
}

$stages = @(
  @{ Name = "npm test"; Body = { npm.cmd test } },
  @{ Name = "npm run build"; Body = { npm.cmd run build } },
  @{
    Name = "pytest evaluation unit tests"
    Body = {
      $python = Join-Path $repoRoot "evaluation\.venv\Scripts\python.exe"
      if (-not (Test-Path -LiteralPath $python)) { $python = "python" }
      # basetemp 必须带 pid：pytest 会在会话开始时清空整个 basetemp 目录，
      # 固定共享名会让并行运行互相删除。finally 中清理，不残留。
      $basetemp = Join-Path $env:TEMP ("PromptCraft-pytest-" + $PID)
      Push-Location (Join-Path $repoRoot "evaluation")
      try {
        & $python -m pytest -q "--basetemp=$basetemp"
      } finally {
        Pop-Location
        if (Test-Path -LiteralPath $basetemp) {
          Remove-Item -LiteralPath $basetemp -Recurse -Force -ErrorAction SilentlyContinue
        }
      }
    }
  },
  @{ Name = "cargo fmt --check"; Body = { cargo fmt --manifest-path $manifest --all -- --check } },
  @{ Name = "cargo test"; Body = { cargo test --manifest-path $manifest } },
  @{ Name = "cargo clippy"; Body = { cargo clippy --manifest-path $manifest --all-targets } },
  @{
    Name = "workspace sanity"
    Body = {
      $status = & git status --porcelain
      if ($LASTEXITCODE -ne 0) { throw "git status failed" }
      $offenders = @()
      foreach ($line in $status) {
        if ($line -match "node_modules/") { $offenders += $line }
        if ($line -match "evaluation/key\.local") { $offenders += $line }
        if ($line -match "\.db") { $offenders += $line }
      }
      # 便携 ZIP 一律不进版本库：直接检查 git 索引，而不是看工作区状态
      # （gitignore 修复后 status 永远为空，属于假安全）。
      $trackedZips = & git ls-files -- "release/*.zip"
      if ($LASTEXITCODE -ne 0) { throw "git ls-files failed" }
      foreach ($zip in $trackedZips) { $offenders += "tracked zip: $zip" }
      if ($offenders.Count -gt 0) {
        Write-Host "工作区状态包含应忽略的缓存/密钥文件：" -ForegroundColor Yellow
        $offenders | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
        throw "workspace sanity check failed"
      }
    }
  }
)

$n = 1
foreach ($stage in $stages) {
  $ok = Invoke-Stage -Index $n -Name $stage.Name -Body $stage.Body
  if (-not $ok) { break }
  $n++
}

$failedResults = @($script:results | Where-Object { -not $_.Ok })
if ($failedResults.Count -gt 0) {
  $passed = @($script:results | Where-Object { $_.Ok }).Count
  $names = ($failedResults | ForEach-Object { "$($_.Name)(CODE)" }) -join ", "
  Write-Host "[verify] SUMMARY: $passed/7 passed; failed=[$names]; environment=OK" -ForegroundColor Red
  exit 1
}

Write-Host "[verify] ALL 7 STAGES PASSED (preflight OK)" -ForegroundColor Green
