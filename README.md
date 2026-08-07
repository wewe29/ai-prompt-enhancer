# PromptCraft

[![CI](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml/badge.svg)](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

PromptCraft 是一款面向 Windows 10/11 的本地 AI 提示词增强器。它分析用户的原始需求和参考资料，最多进行三轮必要澄清，再通过用户自己的 DeepSeek API 生成可编辑的主提示词、修改说明和五个可选补充建议。

项目处于 `0.1.x` 早期版本，欢迎提交问题和改进建议。

## 给第一次使用者和贡献者

这是作者第一次正式开源项目，代码、界面和文档都还在持续改进中，感谢大家多多包涵。欢迎下载体验，并指出你遇到的问题、难用的地方和可以优化的功能。

优先建议在 GitHub 提交 Issue，便于其他用户共同查看和验证；也可以发送邮件至 [3986351310@qq.com](mailto:3986351310@qq.com)。反馈时请不要发送 API Key、密码、公司源码或其他敏感信息。

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

## 下载和使用

从 [GitHub Releases](https://github.com/wewe29/ai-prompt-enhancer/releases) 下载最新 Windows 便携 ZIP，完整解压后运行 `PromptCraft.exe`。

当前版本没有代码签名，Windows SmartScreen 可能显示安全提示。请只从本仓库 Release 页面下载，并核对发布页提供的 SHA-256。

零基础配置和操作说明见 [PromptCraft 使用指南](./PromptCraft使用指南.md)。

## 本地开发

### 环境要求

- Windows 10/11 64 位。
- Node.js 22 或更新版本。
- Rust stable MSVC 工具链。
- Visual Studio 2022 Build Tools 的“使用 C++ 的桌面开发”工作负载。
- 64 位 OpenSSL 开发文件。通过 `OPENSSL_DIR` 指向安装目录；默认会检测 `%ProgramFiles%\OpenSSL-Win64`。

### 安装依赖

```powershell
git clone https://github.com/wewe29/ai-prompt-enhancer.git
cd ai-prompt-enhancer
npm.cmd ci
```

如果所在网络无法稳定访问 crates.io，可以将 `.cargo/config.toml.example` 复制为 `.cargo/config.toml`，启用可选镜像。

### 运行测试

```powershell
npm.cmd test
npm.cmd run build
build-check.cmd test
```

### 启动开发版

```powershell
run-dev.cmd
```

### 构建便携版

```powershell
build-portable.cmd
```

输出位于 `release/`。若要随便携包提供 WebView2 引导程序，请将微软官方 `MicrosoftEdgeWebView2Setup.exe` 放入 `release-assets/`；该二进制文件不会提交到源码仓库。

## 数据和隐私

提示词、上下文及附件提取文字会发送到用户配置的 DeepSeek API。API Key 不写入前端、数据库、普通日志或导出包。项目不收集遥测，也不上传崩溃报告。

详细说明见 [PRIVACY.md](PRIVACY.md)。发现安全问题时请阅读 [SECURITY.md](SECURITY.md)，不要在公开 Issue 中提交密钥或敏感资料。

## 参与贡献

提交 Issue 或 Pull Request 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。版本变化记录在 [CHANGELOG.md](CHANGELOG.md)。第三方组件说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可证

Copyright (C) 2026 wewe29

本项目使用 [GNU General Public License v3.0 only](LICENSE)。分发修改版本时必须遵守 GPL-3.0 的源代码和许可证义务。第三方组件继续适用各自的许可证。
