param([switch]$Test)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "build-env.ps1")
$repoRoot = Split-Path $PSScriptRoot -Parent
$manifest = Join-Path $repoRoot "src-tauri\Cargo.toml"

if ($Test) {
  cargo test --manifest-path $manifest
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  exit 0
}

function Invoke-Stage {
  param([int]$Index, [string]$Name, [scriptblock]$Body)
  Write-Host "[verify] stage $Index/7: $Name"
  $failed = $false
  try {
    & $Body
    if ($LASTEXITCODE -ne 0) { $failed = $true }
  } catch {
    $failed = $true
    Write-Host "  error: $($_.Exception.Message)"
  }
  if ($failed) {
    Write-Host "[verify] FAILED at stage $($Index): $Name" -ForegroundColor Red
    exit 1
  }
  Write-Host ""
}

$stages = @(
  @{ Name = "npm test"; Body = { npm.cmd test } },
  @{ Name = "npm run build"; Body = { npm.cmd run build } },
  @{
    Name = "pytest evaluation unit tests"
    Body = {
      $python = Join-Path $repoRoot "evaluation\.venv\Scripts\python.exe"
      if (-not (Test-Path -LiteralPath $python)) { $python = "python" }
      $basetemp = Join-Path $env:TEMP ("pc-pytest-" + $PID)
      Push-Location (Join-Path $repoRoot "evaluation")
      try {
        & $python -m pytest -q "--basetemp=$basetemp"
      } finally {
        Pop-Location
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
        if ($line -match "release/.*\.zip") { $offenders += $line }
        if ($line -match "evaluation/key\.local") { $offenders += $line }
        if ($line -match "\.db") { $offenders += $line }
      }
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
  Invoke-Stage -Index $n -Name $stage.Name -Body $stage.Body
  $n++
}

Write-Host "[verify] ALL 7 STAGES PASSED" -ForegroundColor Green
