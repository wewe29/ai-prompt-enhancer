# 参与贡献

感谢参与 PromptCraft。请先通过 Issue 说明较大的功能或架构改动，避免重复实现。

这是作者第一次进行公开开源，代码和交互仍可能有不完善之处，欢迎耐心指出问题和可优化的点。一般问题请提交 GitHub Issue；无法公开的内容可以发送至 [3986351310@qq.com](mailto:3986351310@qq.com)。邮件和 Issue 中都不要包含 API Key、密码或真实隐私数据。

## 开发准备

开发环境仅支持 Windows 10/11 64 位。安装 Node.js 22+、Rust stable MSVC、Visual Studio 2022 C++ Build Tools 和 64 位 OpenSSL 开发文件。

```powershell
npm.cmd ci
npm.cmd test
npm.cmd run build
build-check.cmd test
```

## 提交要求

- 保持改动范围清晰，不混入无关重构或生成文件。
- 不提交 API Key、数据库、日志、附件、用户历史或个人绝对路径。
- 新增行为需要按风险补充前端或 Rust 测试。
- 保持中文界面文字清楚，英文代码、错误信息和专有名词原样保留。
- 不扩大 V1 的网页自动化、OCR、云同步和自动执行边界。

## Pull Request

PR 描述应说明问题、解决方式、测试结果、隐私或安全影响，以及是否改变数据格式或用户流程。界面改动请提供截图。

提交 PR 即表示你同意在 GPL-3.0-only 下提供贡献，并确认有权提交相关代码和素材。
