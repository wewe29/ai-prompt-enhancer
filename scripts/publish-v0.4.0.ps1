# v0.4.0 发布脚本（gh 登录后执行）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\publish-v0.4.0.ps1
# 前置：gh auth status 显示已登录
#
# 说明：本脚本只负责「创建 Release + 上传附件」；
# 分支与 tag 的推送由人工先执行（git push origin release/v0.4.0 / git push origin v0.4.0），
# 脚本用 --verify-tag 校验远端确实有这个 tag，避免发一个指向不存在提交的 Release。

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# --- 前置检查 ---
$auth = gh auth status 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Write-Host "[publish] gh 未登录，请先执行 gh auth login" -ForegroundColor Red
    exit 2
}

$zip = "release\PromptCraft-0.4.0-windows-x64-portable.zip"
$sha = "release-assets\SHA256-zip.txt"
$notes = "release-assets\RELEASE_NOTES-v0.4.0.md"
foreach ($f in @($zip, $sha, $notes)) {
    if (-not (Test-Path $f)) { Write-Host "[publish] 缺少文件：$f" -ForegroundColor Red; exit 2 }
}

# 校验 SHA-256 与随包文件一致（防止发布出错包）
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$declared = ((Get-Content $sha -Raw) -split '\s+')[0].ToLower()
if ($actual -ne $declared) {
    Write-Host "[publish] SHA-256 不一致！`n  实际: $actual`n  声明: $declared" -ForegroundColor Red
    exit 1
}
Write-Host "[publish] SHA-256 校验通过：$actual"

# --- 确认远端已有这个 tag（--verify-tag 会自己再查一次，这里给出更清楚的提示）---
$remoteTag = git ls-remote --tags origin refs/tags/v0.4.0 2>&1 | Out-String
if ($remoteTag -notmatch "refs/tags/v0\.4\.0") {
    Write-Host "[publish] 远端没有 v0.4.0 这个 tag。" -ForegroundColor Red
    Write-Host "          请先执行：git push origin release/v0.4.0" -ForegroundColor Yellow
    Write-Host "                        git push origin v0.4.0" -ForegroundColor Yellow
    exit 2
}

# --- 若 Release 已存在则先删除，避免 --verify-tag 冲突 ---
$existing = gh release view v0.4.0 --json tagName 2>$null | Out-String
if ($LASTEXITCODE -eq 0 -and $existing -match 'v0\.4\.0') {
    Write-Host "[publish] v0.4.0 Release 已存在，先删除重建" -ForegroundColor Yellow
    gh release delete v0.4.0 --yes --cleanup-tag | Out-Null
    git push origin v0.4.0 2>&1 | Out-Null
}

# --- 创建 Release ---
$title = "PromptCraft v0.4.0 — 诚实性闭环：41 条「失败显示成成功」全部关掉（不含效果宣称）"
gh release create v0.4.0 `
    --title $title `
    --notes-file $notes `
    --verify-tag `
    $zip `
    $sha `
    --latest

if ($LASTEXITCODE -ne 0) { Write-Host "[publish] 创建失败" -ForegroundColor Red; exit 1 }

Write-Host "`n[publish] Release 创建成功" -ForegroundColor Green
gh release view v0.4.0
