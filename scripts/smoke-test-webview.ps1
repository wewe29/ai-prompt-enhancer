param(
  [int]$Port = 9222,
  [string]$Prompt = ""
)

$ErrorActionPreference = "Stop"
if (-not $Prompt) {
  $Prompt = -join @([char]0x4E2D, [char]0x6691, [char]0x75C7, [char]0x72B6, [char]0x8868, [char]0x73B0)
}
$target = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list")[0]
if (-not $target.webSocketDebuggerUrl) { throw "PromptCraft WebView target not found" }

$socket = [System.Net.WebSockets.ClientWebSocket]::new()
$socket.ConnectAsync([Uri]$target.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
$script:sequence = 0

function Send-CdpText([string]$Text) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
  $segment = [ArraySegment[byte]]::new($bytes)
  $socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
}

function Receive-CdpText {
  $stream = [IO.MemoryStream]::new()
  try {
    do {
      $buffer = New-Object byte[] 65536
      $segment = [ArraySegment[byte]]::new($buffer)
      $result = $socket.ReceiveAsync($segment, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
      $stream.Write($buffer, 0, $result.Count)
    } while (-not $result.EndOfMessage)
    return [Text.Encoding]::UTF8.GetString($stream.ToArray())
  } finally {
    $stream.Dispose()
  }
}

function Invoke-Cdp([string]$Method, [hashtable]$Params = @{}) {
  $script:sequence += 1
  $id = $script:sequence
  Send-CdpText ((@{ id = $id; method = $Method; params = $Params } | ConvertTo-Json -Depth 20 -Compress))
  while ($true) {
    $message = Receive-CdpText | ConvertFrom-Json
    if ($message.id -eq $id) {
      if ($message.error) { throw $message.error.message }
      return $message.result
    }
  }
}

function Invoke-JavaScript([string]$Expression) {
  $response = Invoke-Cdp "Runtime.evaluate" @{ expression = $Expression; returnByValue = $true; awaitPromise = $true }
  if ($response.exceptionDetails) { throw $response.exceptionDetails.text }
  return $response.result.value
}

try {
  $null = Invoke-Cdp "Runtime.enable"
  $summary = Invoke-JavaScript @"
JSON.stringify({
  title: document.title,
  textareas: [...document.querySelectorAll('textarea')].length,
  buttons: [...document.querySelectorAll('button')].map(button => button.innerText.trim()).filter(Boolean),
  configured: Boolean(document.querySelector('.connection.ok'))
})
"@
  "UI_SUMMARY=$summary"

  $promptJson = $Prompt | ConvertTo-Json -Compress
  $setPrompt = Invoke-JavaScript @"
(() => {
  const textarea = document.querySelector('textarea');
  if (!textarea) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(textarea, $promptJson);
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  return textarea.value === $promptJson;
})()
"@
  if (-not $setPrompt) { throw "Unable to set prompt textarea" }

  $clicked = Invoke-JavaScript @"
(() => {
  const button = document.querySelector('.toolbar-controls button.primary:not(.danger)');
  if (!button || button.disabled) return false;
  button.click();
  return true;
})()
"@
  if (-not $clicked) { throw "Enhance button is unavailable" }

  $deadline = [DateTime]::UtcNow.AddSeconds(90)
  do {
    Start-Sleep -Milliseconds 500
    $state = Invoke-JavaScript @"
JSON.stringify({
  status: document.querySelector('.status-badge')?.className ?? '',
  output: document.querySelector('.output-panel textarea')?.value ?? '',
  error: document.querySelector('.error-banner span')?.innerText ?? '',
  suggestions: document.querySelectorAll('.suggestion-grid > button').length
})
"@ | ConvertFrom-Json
    if ($state.error) { throw "APP_ERROR: $($state.error)" }
    if ($state.status -match 'ready|needs_clarification') { break }
  } while ([DateTime]::UtcNow -lt $deadline)

  if ($state.status -notmatch 'ready|needs_clarification') { throw "Timed out with status: $($state.status)" }
  if ([string]::IsNullOrWhiteSpace($state.output)) { throw "Enhancement output is empty" }
  "RESULT_STATUS=$($state.status)"
  "RESULT_LENGTH=$($state.output.Length)"
  "SUGGESTION_COUNT=$($state.suggestions)"
  "RESULT_PREVIEW=$($state.output.Substring(0, [Math]::Min(180, $state.output.Length)).Replace("`r", " ").Replace("`n", " "))"
} finally {
  if ($socket.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
    $socket.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, "done", [Threading.CancellationToken]::None).GetAwaiter().GetResult()
  }
  $socket.Dispose()
}
