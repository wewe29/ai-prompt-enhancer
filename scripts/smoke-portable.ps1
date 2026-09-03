<#
.SYNOPSIS
  对便携版 PromptCraft 做端到端冒烟测试：解压到全新目录、配置 API Key、
  流式生成/停止/重新生成/复制/历史恢复/API 失败保留原文/临时目录残留检查。

.DESCRIPTION
  自动化覆盖规划 §3.2 十步中的可自动化项；SmartScreen 首启体验、WebView2
  引导程序、"复制并打开"实际浏览器效果、附件文件对话框为人工核对项，脚本
  结束时会打印人工核对清单。

  凭据处理：测试会把 deepseek-api-key.PromptCraft 凭据（Windows 凭据管理器，
  与 src-tauri/src/storage.rs 的 keyring 命名一致）备份后替换为 evaluation\key.local
  的评测密钥，结束时恢复原值；失败用例写入假 Key，同样在 finally 中恢复。
  未配置过 Key 时（-FreshAppData 场景）测试结束会删除测试写入的凭据。

  注意：默认非破坏性——冒烟产生的历史记录会落入本机应用数据
  （%APPDATA%\io.github.wewe29.promptcraft），可在应用内删除。
  -FreshAppData 会先删除该目录，属于破坏性操作，仅在确认无重要数据时使用。

.EXAMPLE
  powershell -File scripts\smoke-portable.ps1 -ZipPath release\PromptCraft-0.3.1-windows-x64-portable.zip
#>
param(
  [Parameter(Mandatory = $true)][string]$ZipPath,
  [string]$Prompt = "帮我写一封请假邮件：明天需要请一天病假，收件人是直属上司王经理，原因是感冒发烧需要就医，要附上医院证明，语气正式简洁。",
  [string]$EvalKeyPath = "",
  [int]$Port = 9223,
  [switch]$SkipRealApi,
  [switch]$FreshAppData
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent

if (-not $EvalKeyPath) { $EvalKeyPath = Join-Path $repoRoot "evaluation\key.local" }
$zipFullPath = (Resolve-Path -LiteralPath $ZipPath -ErrorAction Stop).Path
$appDataDir = Join-Path $env:APPDATA "io.github.wewe29.promptcraft"
$credTarget = "deepseek-api-key.PromptCraft"   # keyring Entry::new("PromptCraft","deepseek-api-key") -> username.service

$results = New-Object System.Collections.Generic.List[object]
function Add-StepResult([string]$Name, [bool]$Ok, [string]$Detail) {
  $state = if ($Ok) { "PASS" } else { "FAIL" }
  Write-Host "[smoke] $state  $Name  $Detail" -ForegroundColor $(if ($Ok) { "Green" } else { "Red" })
  $results.Add([pscustomobject]@{ Name = $Name; Ok = $Ok; Detail = $Detail })
}

# ---- Windows 凭据管理器备份 / 恢复（与 keyring windows 后端同构：UTF-16LE blob）----
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class CredMan {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct CREDENTIAL {
    public int Flags;
    public int Type;
    public string TargetName;
    public string Comment;
    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
    public int CredentialBlobSize;
    public IntPtr CredentialBlob;
    public int Persist;
    public int AttributeCount;
    public IntPtr Attributes;
    public string TargetAlias;
    public string UserName;
  }
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool CredReadW(string target, int type, int flags, out IntPtr credPtr);
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool CredWriteW(ref CREDENTIAL cred, int flags);
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool CredDeleteW(string target, int type, int flags);
  [DllImport("advapi32.dll")]
  public static extern void CredFree(IntPtr cred);
}
"@

function Read-StoredApiKey {
  $ptr = [IntPtr]::Zero
  try {
    if (-not [CredMan]::CredReadW($credTarget, 1, 0, [ref]$ptr)) { return $null }
    $cred = [Runtime.InteropServices.Marshal]::PtrToStructure($ptr, [type][CredMan+CREDENTIAL])
    $bytes = New-Object byte[] $cred.CredentialBlobSize
    if ($cred.CredentialBlobSize -gt 0) {
      [Runtime.InteropServices.Marshal]::Copy($cred.CredentialBlob, $bytes, 0, $cred.CredentialBlobSize)
    }
    return @{ Blob = $bytes; UserName = $cred.UserName; Comment = $cred.Comment; Persist = $cred.Persist }
  } finally {
    if ($ptr -ne [IntPtr]::Zero) { [CredMan]::CredFree($ptr) }
  }
}

function Write-StoredApiKey([byte[]]$Blob, [string]$UserName, [string]$Comment, [int]$Persist) {
  $cred = New-Object CredMan+CREDENTIAL
  $cred.Type = 1   # CRED_TYPE_GENERIC
  $cred.TargetName = $credTarget
  $cred.UserName = $UserName
  $cred.Comment = $Comment
  $cred.Persist = $Persist
  $cred.CredentialBlobSize = $Blob.Length
  $cred.CredentialBlob = [Runtime.InteropServices.Marshal]::AllocHGlobal([Math]::Max(1, $Blob.Length))
  try {
    if ($Blob.Length -gt 0) {
      [Runtime.InteropServices.Marshal]::Copy($Blob, 0, $cred.CredentialBlob, $Blob.Length)
    }
    if (-not [CredMan]::CredWriteW([ref]$cred, 0)) {
      throw "CredWriteW 失败：Win32 错误码 $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
  } finally {
    [Runtime.InteropServices.Marshal]::FreeHGlobal($cred.CredentialBlob)
  }
}

$script:originalCred = Read-StoredApiKey
function Restore-StoredApiKey {
  if ($script:originalCred) {
    Write-StoredApiKey -Blob $script:originalCred.Blob -UserName $script:originalCred.UserName `
      -Comment $script:originalCred.Comment -Persist $script:originalCred.Persist
  } else {
    [void][CredMan]::CredDeleteW($credTarget, 1, 0)
  }
}
function Set-StoredApiKey([string]$Key) {
  # keyring windows 后端以 UTF-16LE 存储，Persist 取原值或 CRED_PERSIST_ENTERPRISE(3)。
  $persist = if ($script:originalCred) { $script:originalCred.Persist } else { 3 }
  $userName = if ($script:originalCred -and $script:originalCred.UserName) { $script:originalCred.UserName } else { "deepseek-api-key" }
  Write-StoredApiKey -Blob ([Text.Encoding]::Unicode.GetBytes($Key)) -UserName $userName -Comment "" -Persist $persist
}

# ---- 准备工作目录、密钥、AppData ----
$workDir = Join-Path $env:TEMP ("PromptCraft-smoke-" + $PID)
if (Test-Path -LiteralPath $workDir) { Remove-Item -LiteralPath $workDir -Recurse -Force }
New-Item -ItemType Directory -Path $workDir | Out-Null
$appDir = Join-Path $workDir "app"

$evalKey = $null
if (-not $SkipRealApi) {
  if (-not (Test-Path -LiteralPath $EvalKeyPath)) {
    throw "未找到评测密钥 $EvalKeyPath，无法执行真实 API 用例；确认密钥路径或改用 -SkipRealApi。"
  }
  $evalKey = ([IO.File]::ReadAllText($EvalKeyPath)).Trim()
  if (-not $evalKey) { throw "评测密钥文件 $EvalKeyPath 为空。" }
}

if ($FreshAppData) {
  if (Test-Path -LiteralPath $appDataDir) {
    Write-Host "[smoke] -FreshAppData：删除 $appDataDir（破坏性操作）" -ForegroundColor Yellow
    Remove-Item -LiteralPath $appDataDir -Recurse -Force
  }
}

Write-Host "[smoke] 解压 $zipFullPath"
Expand-Archive -LiteralPath $zipFullPath -DestinationPath $appDir -Force
$exePath = Join-Path $appDir "PromptCraft.exe"
if (-not (Test-Path -LiteralPath $exePath)) { throw "解压后未找到 PromptCraft.exe" }

# %TEMP% 快照：只关注应用运行期间新增的 PromptCraft-* 目录（附件/缓存残留检查）。
$beforeTemp = @(Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter "PromptCraft-*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)

# ---- 启动应用（打开 CDP 调试端口）----
$prevDebugArgs = $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=$Port"
Write-Host "[smoke] 启动 $exePath（CDP 端口 $Port）"
$proc = Start-Process -FilePath $exePath -WorkingDirectory $appDir -PassThru

$socket = $null
$script:sequence = 0

function Connect-Cdp {
  $deadline = [DateTime]::UtcNow.AddSeconds(60)
  while ([DateTime]::UtcNow -lt $deadline) {
    try {
      $targets = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list" -ErrorAction Stop)
      $page = $targets | Where-Object { $_.type -eq "page" -and $_.webSocketDebuggerUrl } | Select-Object -First 1
      if ($page) {
        $script:socket = [System.Net.WebSockets.ClientWebSocket]::new()
        $script:socket.ConnectAsync([Uri]$page.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
        if ($script:socket.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
          $null = Invoke-Cdp "Runtime.enable"
          return $true
        }
      }
    } catch {
      if ($script:socket) { $script:socket.Dispose(); $script:socket = $null }
      Start-Sleep -Milliseconds 800
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Send-CdpText([string]$Text) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
  $script:socket.SendAsync([ArraySegment[byte]]::new($bytes), [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
}

function Receive-CdpText {
  $stream = [IO.MemoryStream]::new()
  try {
    do {
      $buffer = New-Object byte[] 65536
      $result = $script:socket.ReceiveAsync([ArraySegment[byte]]::new($buffer), [Threading.CancellationToken]::None).GetAwaiter().GetResult()
      $stream.Write($buffer, 0, $result.Count)
    } while (-not $result.EndOfMessage)
    return [Text.Encoding]::UTF8.GetString($stream.ToArray())
  } finally { $stream.Dispose() }
}

function Invoke-Cdp([string]$Method, [hashtable]$Params = @{}) {
  $script:sequence += 1
  $id = $script:sequence
  Send-CdpText ((@{ id = $id; method = $Method; params = $Params } | ConvertTo-Json -Depth 20 -Compress))
  while ($true) {
    $message = Receive-CdpText | ConvertFrom-Json
    if ($message.id -eq $id) {
      if ($message.error) { throw "CDP $Method 失败：$($message.error.message)" }
      return $message.result
    }
  }
}

function Invoke-JavaScript([string]$Expression) {
  $response = Invoke-Cdp "Runtime.evaluate" @{ expression = $Expression; returnByValue = $true; awaitPromise = $true }
  if ($response.exceptionDetails) { throw "JS 异常：$($response.exceptionDetails.text) $($response.exceptionDetails.exception.description)" }
  return $response.result.value
}

function Wait-JsTrue([string]$Expression, [int]$TimeoutSeconds = 60, [int]$PollMs = 500) {
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    try {
      if (Invoke-JavaScript $Expression) { return $true }
    } catch { Start-Sleep -Milliseconds $PollMs }
    Start-Sleep -Milliseconds $PollMs
  } while ([DateTime]::UtcNow -lt $deadline)
  return $false
}

function Navigate-To([string]$Title) {
  $null = Invoke-JavaScript @"
(() => {
  const button = [...document.querySelectorAll('aside.sidebar nav button')].find((b) => b.title === '$Title');
  if (!button) return false;
  button.click();
  return true;
})()
"@
}

try {
  if (-not (Connect-Cdp)) { throw "无法通过 CDP 连接应用（端口 $Port）。检查 WebView2 环境或 SmartScreen 拦截。" }
  Add-StepResult "启动应用并建立 CDP 连接" $true "pid=$($proc.Id)"

  # ---- S1 UI 结构 ----
  $summary = Invoke-JavaScript @"
JSON.stringify({
  title: document.title,
  textareas: document.querySelectorAll('textarea').length,
  navTitles: [...document.querySelectorAll('aside.sidebar nav button')].map((b) => b.title),
  hasOutputPanel: Boolean(document.querySelector('.output-panel'))
})
"@ | ConvertFrom-Json
  Add-StepResult "UI 启动结构" ($summary.title -eq "PromptCraft" -and $summary.textareas -ge 2 -and $summary.navTitles -contains "设置") "title=$($summary.title), textareas=$($summary.textareas)"

  # ---- S2 配置 API Key（真实密钥走“验证并保存”的真实校验调用）----
  Navigate-To "设置"
  $null = Wait-JsTrue -Expression "Boolean(document.querySelector('.settings-actions'))" -TimeoutSeconds 10
  $configured = Invoke-JavaScript "Boolean(document.querySelector('.connection.ok'))"
  if ($configured) {
    Add-StepResult "API Key 配置状态" $true "应用已有已配置凭据，跳过写入"
  } elseif ($SkipRealApi) {
    Add-StepResult "API Key 配置状态" $false "未配置且 -SkipRealApi 跳过写入（真实 API 用例将失败）"
  } else {
    $null = Invoke-JavaScript @"
(() => {
  const input = document.querySelector('.settings-section input[type=password]');
  if (!input) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, $(($evalKey | ConvertTo-Json -Compress)));
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return input.value.length > 0;
})()
"@
    $null = Invoke-JavaScript "document.querySelector('.settings-actions button.primary').click(); true"
    $saved = Wait-JsTrue -Expression "Boolean(document.querySelector('.connection.ok')) || (document.querySelector('.settings-actions span') && document.querySelector('.settings-actions span').innerText.includes('连接验证成功'))" -TimeoutSeconds 40
    Add-StepResult "写入并验证评测 API Key" $saved "凭据管理器 deepseek-api-key.PromptCraft"
  }

  # ---- S3 流式生成 + 停止 ----
  Navigate-To "增强"
  $null = Wait-JsTrue -Expression "Boolean(document.querySelector('.toolbar-controls button.primary'))" -TimeoutSeconds 10
  $promptJson = $Prompt | ConvertTo-Json -Compress
  $setOriginal = Invoke-JavaScript @"
(() => {
  const textarea = document.querySelector('.editor-panel:not(.output-panel) > textarea');
  if (!textarea) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(textarea, $promptJson);
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  return textarea.value === $promptJson;
})()
"@
  Add-StepResult "写入原始需求" $setOriginal ""

  if ($SkipRealApi) {
    Add-StepResult "流式生成/停止/重新生成" $false "-SkipRealApi 跳过真实 API 用例"
    Add-StepResult "复制结果到剪贴板" $false "-SkipRealApi 跳过"
    Add-StepResult "历史记录保存与恢复" $false "-SkipRealApi 跳过"
    Add-StepResult "API 失败时原文保留" $false "-SkipRealApi 跳过"
  } else {
    $null = Invoke-JavaScript "document.querySelector('.toolbar-controls button.primary:not(.danger)').click(); true"
    $streaming = Wait-JsTrue -Expression "document.querySelector('.status-badge') && document.querySelector('.status-badge').className.includes('streaming')" -TimeoutSeconds 30
    $stopped = "no-click"
    if ($streaming) {
      $null = Invoke-JavaScript "document.querySelector('.toolbar-controls button.primary.danger').click(); true"
      $stopped = Wait-JsTrue -Expression "document.querySelector('.status-badge') && !document.querySelector('.status-badge').className.includes('streaming')" -TimeoutSeconds 30
    }
    $inputKept = Invoke-JavaScript @"
(() => {
  const textarea = document.querySelector('.editor-panel:not(.output-panel) > textarea');
  return textarea && textarea.value === ($promptJson);
})()
"@
    Add-StepResult "流式生成后停止" ($streaming -and $stopped -and $inputKept) "streaming=$streaming, stopped=$stopped, 原文保留=$inputKept"

    # ---- S4 重新生成（完整跑完）----
    $regenButton = "[...document.querySelectorAll('.result-footer button')].find((b) => b.innerText.includes('重新生成'))"
    $null = Invoke-JavaScript "$regenButton.click(); true"
    $done = Wait-JsTrue -Expression @"
(() => {
  const badge = document.querySelector('.status-badge');
  return badge && (badge.className.includes('ready') || badge.className.includes('needs_clarification'));
})()
"@ -TimeoutSeconds 150
    $output = Invoke-JavaScript "document.querySelector('.output-panel textarea') ? document.querySelector('.output-panel textarea').value : ''"
    Add-StepResult "重新生成完整交付" ($done -and -not [string]::IsNullOrWhiteSpace($output)) "status-done=$done, 输出 $($output.Length) 字"

    # ---- S5 复制结果 ----
    $null = Invoke-JavaScript "[...document.querySelectorAll('.result-footer button')].find((b) => b.innerText.trim() === '复制').click(); true"
    Start-Sleep -Milliseconds 800
    $clipboard = Get-Clipboard -Raw
    Add-StepResult "复制结果到剪贴板" ($clipboard -and $clipboard.Trim() -eq $output.Trim()) "剪贴板 $($clipboard.Length) 字"

    # ---- S6 历史记录保存与恢复 ----
    Navigate-To "历史"
    $hasHistory = Wait-JsTrue -Expression "document.querySelectorAll('.history-main').length >= 1" -TimeoutSeconds 10
    $restored = $false
    if ($hasHistory) {
      $null = Invoke-JavaScript "document.querySelector('.history-main').click(); true"
      Start-Sleep -Milliseconds 600
      $restored = Invoke-JavaScript @"
(() => {
  const textarea = document.querySelector('.editor-panel:not(.output-panel) > textarea');
  const output = document.querySelector('.output-panel textarea');
  return Boolean(textarea && textarea.value === ($promptJson) && output && output.value.length > 0);
})()
"@
    }
    Add-StepResult "历史记录保存与恢复" ($hasHistory -and $restored) "存在=$hasHistory, 恢复一致=$restored"

    # ---- S7 API 失败时原文保留（写入假 Key 触发 AUTH 失败，结束在 finally 中恢复真凭据）----
    Set-StoredApiKey "sk-promptcraft-smoke-invalid"
    $null = Invoke-JavaScript "document.querySelector('.toolbar-controls button.primary:not(.danger)').click(); true"
    $failedShown = Wait-JsTrue -Expression @"
(() => {
  const banner = document.querySelector('.error-banner span');
  const badge = document.querySelector('.status-badge');
  return Boolean((banner && banner.innerText.trim()) || (badge && badge.className.includes('error')));
})()
"@ -TimeoutSeconds 60
    $inputKeptAfterFail = Invoke-JavaScript @"
(() => {
  const textarea = document.querySelector('.editor-panel:not(.output-panel) > textarea');
  return textarea && textarea.value === ($promptJson);
})()
"@
    Add-StepResult "API 失败时原文保留" ($failedShown -and $inputKeptAfterFail) "错误提示=$failedShown, 原文保留=$inputKeptAfterFail"
  }

  # ---- S8 临时目录残留检查 ----
  $afterTemp = @(Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter "PromptCraft-*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
  $newDirs = @($afterTemp | Where-Object { $beforeTemp -notcontains $_ -and $_ -ne (Split-Path $workDir -Leaf) })
  Add-StepResult "运行期间无新增 PromptCraft-* 临时目录" ($newDirs.Count -eq 0) "新增=[$($newDirs -join ', ')]（附件提取为内存操作，不落盘）"
} finally {
  # ---- 恢复凭据、杀进程、清理目录（任何路径都要执行）----
  try { Restore-StoredApiKey; Write-Host "[smoke] 已恢复凭据管理器原值" } catch { Write-Host "[smoke] 凭据恢复失败：$($_.Exception.Message)" -ForegroundColor Red }
  if ($proc -and -not $proc.HasExited) {
    Start-Process -FilePath "taskkill.exe" -ArgumentList "/PID $($proc.Id) /T /F" -WindowStyle Hidden -Wait
  }
  if ($socket) {
    try {
      if ($socket.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
        $socket.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, "done", [Threading.CancellationToken]::None).GetAwaiter().GetResult()
      }
      $socket.Dispose()
    } catch { }
  }
  if ($null -ne $prevDebugArgs) { $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $prevDebugArgs } else { Remove-Item Env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS -ErrorAction SilentlyContinue }
  if (Test-Path -LiteralPath $workDir) { Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue }
}

# ---- 汇总 ----
$failed = @($results | Where-Object { -not $_.Ok })
Write-Host ""
Write-Host "[smoke] 自动化结果：$($results.Count - $failed.Count)/$($results.Count) 通过" -ForegroundColor $(if ($failed.Count) { "Red" } else { "Green" })
Write-Host "[smoke] 人工核对清单：" -ForegroundColor Yellow
Write-Host "  1. 首次运行 SmartScreen 提示（发布说明已声明未签名风险）"
Write-Host "  2. 无 WebView2 环境时引导程序 MicrosoftEdgeWebView2Setup.exe 的安装体验"
Write-Host "  3. 复制并打开目标网页：默认浏览器实际打开与剪贴板内容"
Write-Host "  4. 附件对话框添加 TXT/PDF/DOCX 后提取与移除（提取为内存操作，无临时文件）"
Write-Host "  5. 本机历史记录中冒烟产生的条目可在应用内删除"
if ($failed.Count) { exit 1 }
exit 0
