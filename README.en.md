# PromptCraft

[![CI](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml/badge.svg)](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/wewe29/ai-prompt-enhancer)](https://github.com/wewe29/ai-prompt-enhancer/releases)

> English version. For the Chinese guide, see [`README.md`](./README.md) and [`PromptCraft使用指南.md`](./PromptCraft使用指南.md).

PromptCraft is a local AI prompt enhancer for Windows 10/11. It analyses your raw requirement and any reference materials you attach, asks up to three rounds of clarifying questions when the input is ambiguous, and then calls your own DeepSeek API to generate an editable main prompt, a list of changes versus the original, and five optional supplementary suggestions.

This is the author's first public open-source release. The codebase, UI and documentation are still rough around the edges; feedback and pull requests are welcome.

## Features

- DeepSeek Chat and `deepseek-v4-flash` model switching.
- Streaming generation, stop, automatic retry, and up to three rounds of clarification.
- Side-by-side view of the original and enhanced prompt with per-item accept / reject.
- Manual editing, undo / redo, and five optional supplementary suggestions.
- Local text extraction from TXT, code, text-layer PDF and DOCX attachments.
- API key stored in the Windows credential manager.
- SQLCipher-encrypted local history with import / export and clean-up.
- Cost estimation, soft budget reminders, and hard budget limits.
- One-click "copy and open" to Doubao, DeepSeek, Qwen, MiniMax or a custom page.

## Product boundaries

- PromptCraft only enhances prompts. It does not generate the final answer to the underlying question.
- The application never takes control of a target model's web page and never pastes or sends content on your behalf.
- The current version does not support screenshot OCR, scanned PDF, Excel, PowerPoint, archives, or project folders.
- You supply your own API key, top up your account, and bear the model call cost.
- PromptCraft does not search the web and does not fact-check the prompt content.

## Download and usage

Download the latest Windows portable ZIP from [GitHub Releases](https://github.com/wewe29/ai-prompt-enhancer/releases). Unzip the entire archive and run `PromptCraft.exe`.

The 0.1.x release is not code-signed, so Windows SmartScreen may show a security prompt. Only download from this repository's Release page and verify the published SHA-256 hashes.

Zero-to-running instructions: see [`PromptCraft使用指南.md`](./PromptCraft使用指南.md) (Chinese) — the same steps apply to English users, just substitute your own DeepSeek account and language.

## Local development

### Requirements

- Windows 10 / 11 64-bit.
- Node.js 22 or newer.
- Rust stable MSVC toolchain.
- Visual Studio 2022 Build Tools with the "Desktop development with C++" workload.
- 64-bit OpenSSL development files. Point `OPENSSL_DIR` at the install root, otherwise the build looks for `%ProgramFiles%\OpenSSL-Win64`.

### Install dependencies

```powershell
git clone git@github.com:wewe29/ai-prompt-enhancer.git
cd ai-prompt-enhancer
npm.cmd ci
```

If `crates.io` is unreachable from your network, copy `.cargo/config.toml.example` to `.cargo/config.toml` to enable the optional mirror.

### Run tests

```powershell
npm.cmd test
npm.cmd run build
build-check.cmd test
```

### Launch the development build

```powershell
run-dev.cmd
```

### Build the portable release

```powershell
build-portable.cmd
```

The output is written to `release/`. To ship a WebView2 bootstrapper with the portable bundle, drop Microsoft's `MicrosoftEdgeWebView2Setup.exe` into `release-assets/`. That binary is intentionally not committed to the source repository.

## Data and privacy

Your prompt, in-app context, and extracted attachment text are sent to the DeepSeek endpoint you configured. The API key is never written to the front-end, database, ordinary logs, or export bundles. The application collects no telemetry and uploads no crash reports.

See [PRIVACY.md](PRIVACY.md) for the full statement. For security issues, read [SECURITY.md](SECURITY.md) — never post keys or sensitive material to a public issue.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request. Version history lives in [CHANGELOG.md](CHANGELOG.md). Third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

Copyright (C) 2026 wewe29

This project is licensed under the [GNU General Public License v3.0 only](LICENSE). Distributing a modified version requires that you honour the source and licence obligations of GPL-3.0. Third-party components remain under their respective licences.
