# PromptCraft 0.1.0 - 首次开源发布

首个面向用户的稳定版本，提供本地提示词增强、附件文本提取、加密历史和费用估算。

## 下载

主要资产：

- **`PromptCraft-0.1.0-windows-x64-portable.zip`** — Windows 10/11 64 位便携版，10.58 MB。解压后双击 `PromptCraft.exe` 即可使用，不需要 Python、Node.js 或任何开发工具。

可选附加：

- `MicrosoftEdgeWebView2Setup.exe` — 微软官方 WebView2 Runtime 引导器。便携包同目录携带时，软件会在首次启动时静默安装 WebView2。如果你的系统已自带 WebView2（例如 Windows 11 23H2 之后），可以忽略这个文件。
- `README.txt` — 资产目录说明。

## SHA-256 校验

```
B4B42F959F0AE6A7D47C79EEAFA39438265B73A9661872EBBDE05D13501CE3FF  PromptCraft-0.1.0-windows-x64-portable.zip
E99838C51BB3379B244654AA77E33032D42FC2B5D224C5BABCE432D9FD3DCB28  MicrosoftEdgeWebView2Setup.exe
```

PowerShell 校验示例：

```powershell
Get-FileHash .\PromptCraft-0.1.0-windows-x64-portable.zip -Algorithm SHA256
Get-FileHash .\MicrosoftEdgeWebView2Setup.exe -Algorithm SHA256
```

## 重要安全提示

- 当前版本**没有代码签名**。Windows SmartScreen 首次启动会弹出安全警告。
- 请只从本仓库 Release 页面下载，并核对上面 SHA-256 校验值。
- 软件不发送任何遥测，不会上传崩溃报告。详细说明见 [PRIVACY.md](https://github.com/wewe29/ai-prompt-enhancer/blob/main/PRIVACY.md)。

## 主要功能

- DeepSeek Chat 与 `deepseek-v4-flash` 模型切换。
- 流式生成、停止、自动重试和最多三轮澄清。
- 原文与增强结果对照，逐项接受或拒绝修改。
- 手动编辑、撤销、重做和五个可选补充建议。
- TXT、代码、带文字层 PDF 和 DOCX 本地文本提取。
- API Key 存入 Windows 凭据管理器。
- SQLCipher 本地加密历史、数据导入导出和清理。
- 费用估算、提醒额度和强制额度。
- 复制提示词并打开豆包、DeepSeek、千问、MiniMax 或自定义网页。

## 产品边界

- PromptCraft 只增强提示词，不生成目标问题的最终答案。
- 软件不会自动控制目标模型网页，也不会自动粘贴或发送内容。
- 当前版本不支持截图 OCR、扫描版 PDF、Excel、PPT、压缩包和项目文件夹。
- 用户自行提供 API Key、充值并承担模型调用费用。
- 软件不联网搜索或核验提示词中的事实。

## 反馈

- 优先在 GitHub 提交 Issue，便于其他用户共同查看和验证。
- 也可以发送邮件至 `3986351310@qq.com`。**反馈时请删除 API Key、密码和其他敏感信息**。
- 详细说明见 [`PromptCraft 使用指南`](https://github.com/wewe29/ai-prompt-enhancer/blob/main/PromptCraft使用指南.md) 和 [`README`](https://github.com/wewe29/ai-prompt-enhancer)。

## 许可证

Copyright (C) 2026 wewe29

本项目使用 [GNU General Public License v3.0 only](https://github.com/wewe29/ai-prompt-enhancer/blob/main/LICENSE)。
