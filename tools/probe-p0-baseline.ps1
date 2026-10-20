<#
.SYNOPSIS
    PromptCraft v0.4.0 · WP0 基线可用性端到端探针（P0-1 / P0-2 / P0-3 / P0-4）。

.DESCRIPTION
    一条命令跑完三个发布阻断的端到端验收，输出可直接贴进评审材料。

    探针走**真实命令层**（Tauri IPC 的参数反序列化 + 真实命令处理函数），
    并在本机起 mock HTTP 端点抓包：
      P0-1  保存 Key → 重新打开（模拟重启）→ has_api_key == true
            → 用该 Key 对 mock 端点发一次增强 → 断言 Authorization 头确实是该 Key
      P0-2  导入含攻击者 baseUrl 的包（攻击者端点跑在本机 HTTP 上，
            连 base_url 的 HTTPS 校验都能过）→ 断言端点没被改写、
            攻击者端点收到 0 个请求、真实 Key 不出现在任何外发请求里
      P0-3  造 600 条历史 → 导出 → 断言导出条数 == 总条数
      P0-4  静态断言：provider.rs 的温度注释不得把噪声地板内的单点观测
            写成已证实的改进（必须保留 1.65 标准误 / 噪声地板 / 无偏估计 50.5% / 方向未确认）

    探针实现在 src-tauri\src\probe.rs（只在测试构建里存在）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\probe-p0-baseline.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\probe-p0-baseline.ps1 -KeepOutput
#>
[CmdletBinding()]
param(
    # 保留 cargo 的原始输出（默认只打印探针自己的结论行）
    [switch]$KeepOutput
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $repoRoot 'src-tauri\Cargo.toml'

Write-Host '=== PromptCraft v0.4.0 · WP0 基线可用性探针 ===' -ForegroundColor Cyan
Write-Host "仓库：$repoRoot"

# 编译 Rust 需要 OpenSSL：必须用项目自带脚本（它会同时设好
# OPENSSL_DIR / OPENSSL_INCLUDE_DIR / OPENSSL_LIB_DIR）。
# 手动只设 OPENSSL_DIR 会让 libsqlite3-sys 回落到空的 lib\ 目录 → LNK1181。
$buildEnv = Join-Path $repoRoot 'scripts\build-env.ps1'
if (-not (Test-Path -LiteralPath $buildEnv)) {
    throw "ENVIRONMENT: 找不到 $buildEnv"
}
. $buildEnv
# build-env.ps1 会把 $ErrorActionPreference 置成 Stop，这里恢复成 Continue，
# 否则 cargo 往 stderr 写一行进度就会被当成终止错误。
$ErrorActionPreference = 'Continue'

if (-not $env:CARGO_TARGET_DIR) {
    $env:CARGO_TARGET_DIR = Join-Path $env:TEMP 'PromptCraft-cargo-target'
}
Write-Host "CARGO_TARGET_DIR=$($env:CARGO_TARGET_DIR)"
Write-Host ''

$logPath = Join-Path $env:TEMP ('PromptCraft-wp0-probe-' + [guid]::NewGuid().ToString('N') + '.log')

Push-Location $repoRoot
try {
    # 只跑探针：probe_p0_ 前缀的三个用例。--nocapture 让探针自己打印结论行。
    cargo test --manifest-path $manifest probe_p0 -- --nocapture --test-threads=1 *>&1 |
        Tee-Object -FilePath $logPath
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

Write-Host ''
if ($exitCode -eq 0) {
    Write-Host '=== 探针结果：全部通过 ===' -ForegroundColor Green
}
else {
    Write-Host "=== 探针结果：失败（exit=$exitCode）===" -ForegroundColor Red
}
Write-Host "完整输出：$logPath"

if (-not $KeepOutput) {
    Write-Host ''
    Write-Host '--- 探针结论行 ---' -ForegroundColor Cyan
    Select-String -Path $logPath -Pattern '\[P0-\d\]' | ForEach-Object { $_.Line.Trim() }
}

exit $exitCode
