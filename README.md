# PromptCraft

[![CI](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml/badge.svg)](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/wewe29/ai-prompt-enhancer)](https://github.com/wewe29/ai-prompt-enhancer/releases)

> 📖 **[English version →](./README.en.md)**

PromptCraft 是一款面向 Windows 10/11 的本地 AI 提示词增强器。它分析用户的原始需求和参考资料，最多进行三轮必要澄清，再通过用户自己的 API 生成可编辑的主提示词、修改说明和 0-5 条可选补充建议，并在模型返回结构异常时保留可交付结果（部分交付/原文回退）。

项目处于 `0.3.x` 迭代阶段，欢迎提交问题和改进建议。

## 给第一次使用者和贡献者

这是作者第一次正式开源项目，代码、界面和文档都还在持续改进中，感谢大家多多包涵。欢迎下载体验，并指出你遇到的问题、难用的地方和可以优化的功能。

**效果说明（2026-09 v0.3.2 复评，60 条三档模糊样本 × 4 目标模型 × 3 次重复 + 48 单元人工盲评）**：按发布门槛规则，本轮仍未达到"可写量化效果数字"的标准（严重/中等模糊任务质量提升、清晰任务变差率、膨胀比、双裁判一致率均不达标），因此只作定性描述——增强后的回答通常更长、结构更完整，但**盲态人工对比未显示质量优势**（48 个盲评单元中 0 个判增强回答更好，43.8% 判接近、56.3% 判原文更好）；自动评测胜率偏高的主要原因是 LLM 裁判对长回答的系统性偏好。**已经写清楚的任务请优先直接使用原文**（清晰任务被过度改动的风险两个评测口径一致确认）；对信息严重缺失的任务可以尝试增强，但结果需要逐项审核后采用。完整报告见 [docs/PromptCraft-v0.3.2-评测报告.md](docs/PromptCraft-v0.3.2-评测报告.md)。

**增强能力验证（v0.3.5，60 条样本 × DeepSeek-V4.1-Flash 底模）**：换用 V4.1-Flash 底模后重跑 60 条三档样本，档位判断 **60/60 全部恰当**——严重模糊 20 条**全部转为澄清**（并列出待确认清单 + 基于明确假设的临时方案，不编造事实）；中等模糊 17 条轻度补齐、3 条判定无需改动；**清晰档 20 条里 11 条原样返回**，仅做轻量排版。指令越权 0 例。结论是：**这个增强器知道自己什么时候不该动手**。已写清楚目标/背景/约束/格式的任务，直接用原文即可。

> 上述验证由不同模型分别承担"增强"与"评审"（非自我评分），但**没有做人工盲评**，不能等同于人的判断，也不应与 v0.3.2 结论直接比较（底模与评审方式均已变更）。详见 [docs/PromptCraft-增强能力验证报告-v0.3.5.md](docs/PromptCraft-增强能力验证报告-v0.3.5.md) 与评审规则 [docs/PromptCraft-增强器评审规则-单模型版.md](docs/PromptCraft-增强器评审规则-单模型版.md)。

优先建议在 GitHub 提交 Issue，便于其他用户共同查看和验证；也可以发送邮件至 [3986351310@qq.com](mailto:3986351310@qq.com)。反馈时请不要发送 API Key、密码、公司源码或其他敏感信息。

## v0.3.4 / v0.3.5 安全与数据可靠性加固

这两次迭代集中处理了导入、凭据、附件与本地数据四条线上的隐患，**依据单模型代码评审的实证结论修复**（评审报告见 [docs/PromptCraft-v0.3.4-评审报告.md](docs/PromptCraft-v0.3.4-评审报告.md)）：

- **导入的数据包永远无法改写你的 API Key。** `provider.json` 里的 `apiKey` 字段在反序列化方向被显式忽略，并在导入流程中再清空一次。此前一个来源不明的 ZIP 就能把你的 DeepSeek Key 静默替换成攻击者的值（已实测复现并修复），导致提示词内容与费用一并转移。
- **置顶功能真正可用。** 置顶记录**永远不会被** 90 天时间清理与容量清理删除（历史页有置顶按钮，置顶项排最前并高亮）。
- **历史容量上限可配置。** 设置页新增"历史容量上限（MB）"（默认 64 MB，范围 1–1024），超出后按**最旧的未置顶记录**逐条清理直到达标。
- **ZIP 导入加固**：阻止路径穿越、限制文件数与总大小、单条目解压硬上限 8 MB（防压缩包炸弹）。
- **附件安全失败**：空文件、超大文件、损坏的 PDF/DOCX、二进制伪装文本均安全报错，不崩溃。附件为原地读取，**不产生任何临时文件**。
- **错误提示不泄露凭据**：服务端回显内容统一过凭据遮蔽，API Key 不会出现在任何错误信息里。
- **测试不再污染你的凭据管理器**：测试使用隔离的临时目录与凭据服务名，并在结束时自动清理（此前每跑一次测试就往系统凭据管理器追加一批 `PromptCraftTest-*` 条目）。

## 主要功能

- 自定义模型列表（设置页配置，增强页下拉选择）。
- 任务类型识别与差异化增强（代码/创意/写作/问答解释/数据分析/翻译/其他）。
- 流式生成、停止、自动重试和最多三轮澄清。
- 原文与增强结果对照，逐项接受或拒绝修改。
- 发送前快速检查（本地离线、零成本）。
- 一键套用画像预设（纯AI小白/学生/普通办公员工/程序员）。
- 手动编辑、撤销、重做（Ctrl+Z/Ctrl+Shift+Z·Ctrl+Y）、快捷发送（Ctrl+Enter）和关闭（Esc）。
- 五个可选补充建议（0-5 条）。
- 增强等级判定（无需明显修改/轻度增强/需要澄清）与任务感知的发送前快速检查。
- 结果区显示交付状态徽标、用户原始事实、风险提示与影响范围、候选提示词切换（1-3 个候选）。
- 修改明细支持逐项或一键接受/拒绝；partial 交付清单化告知缺失字段；澄清显示当前与剩余轮次。
- 历史记录支持**置顶**（置顶项永不被自动清理）与**容量上限配置**。
- 交付降级保障：模型返回结构异常时自动部分交付或回退原文，失败不丢输入。
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

#### OpenSSL 依赖

Rust 构建需要 64 位 OpenSSL **开发文件**（`include\openssl\ssl.h` 与 `lib` 目录），只装运行库不够：

```powershell
winget install ShiningLight.OpenSSL
setx OPENSSL_DIR "C:\Program Files\OpenSSL-Win64"
```

`setx` 之后需要重新打开终端才会生效。`scripts\build-env.ps1` 会按顺序探测 `%OPENSSL_DIR%`、`%ProgramFiles%\OpenSSL-Win64`、`%ProgramFiles(x86)%\OpenSSL-Win64`、`%ProgramFiles%\OpenSSL`、`%LOCALAPPDATA%\Programs\OpenSSL-Win64`；全部未命中时输出探测清单与上述安装命令。已有自定义安装时，把 `OPENSSL_DIR` 指向该目录即可。CI 上使用 vcpkg 的 `openssl:x64-windows`（见 `.github/workflows/ci.yml`）。

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
