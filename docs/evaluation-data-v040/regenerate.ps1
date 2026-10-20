# 一键重跑 v0.4.0 的三次测量实验。
#
# 目的：把「结论」和「结论的复现材料」拉到同一个版本上。
# 原始数据随 F 盘丢失后，v0.4.0 的每一个数字都无法复核（P0-4）；
# 本脚本是补回原始数据的入口。
#
# 成本：三次实验合计约 440 次 API 调用。**跑之前请确认 DEEPSEEK_API_KEY 与余额。**
#
# 用法：
#   pwsh -File docs/evaluation-data-v040/regenerate.ps1
#   pwsh -File docs/evaluation-data-v040/regenerate.ps1 -What "stability"   # 只跑其中一项

param(
    [ValidateSet("all", "stability", "severe_blind", "vote")]
    [string]$What = "all",
    [switch]$SkipApiCheck
)

$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $Root

function Step($name, $block) {
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
    & $block
    if ($LASTEXITCODE -ne 0) {
        throw "$name 失败（退出码 $LASTEXITCODE）"
    }
}

if (-not $SkipApiCheck) {
    if (-not $env:DEEPSEEK_API_KEY) {
        Write-Host "ERROR: DEEPSEEK_API_KEY 未设置。原始数据再生需要真实 API 调用。" -ForegroundColor Red
        Write-Host "       若只想重算指标（不调 API），用 --judge-only 重算已有产物。" -ForegroundColor Yellow
        exit 2
    }
}

if ($What -in @("all", "stability")) {
    Step "1/3 档位稳定性（60 条 × 3 次 = 180 次调用）" {
        py -3 evaluation\verify_stability.py --repeats 3
    }
}

if ($What -in @("all", "severe_blind")) {
    Step "2/3 severe 澄清质量盲评（20 次调用 + 人工评审）" {
        py -3 evaluation\verify_enhance.py
        $raw = "evaluation\results\v035_enhance_raw.json"
        if (-not (Test-Path $raw)) { throw "缺少 $raw" }
        py -3 evaluation\v040_severe_blind.py export --raw $raw --outdir evaluation\results\blind_v040
    }
    Write-Host ""
    Write-Host ">>> 接下来需要人工评审（这一步脚本做不了）：" -ForegroundColor Yellow
    Write-Host "    1) 打开 evaluation\results\blind_v040\blind_packet.md"
    Write-Host "    2) 逐条填写「其中几个是不回答就没法完成任务的」"
    Write-Host "    3) 存成 evaluation\results\blind_v040\blind_answers.json"
    Write-Host "    4) 再跑："
    Write-Host "       py -3 evaluation\v040_severe_blind.py parse --answers evaluation\results\blind_v040\blind_answers.json --key evaluation\results\blind_v040\blind_key.json"
    Write-Host "       py -3 evaluation\v040_severe_blind.py aggregate --scored evaluation\results\blind_v040\scored.json"
}

if ($What -in @("all", "vote")) {
    Step "3/3 档位投票（240 次调用）" {
        py -3 evaluation\verify_vote_stability.py
    }
}

Write-Host ""
Write-Host "完成。产物在 evaluation\results\ 下，目录名带身份标签，不会覆盖历史结果。" -ForegroundColor Green
Write-Host "对账请比对 docs\evaluation-data-v040\conclusions\*.json。" -ForegroundColor Green
