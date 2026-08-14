# PromptCraft

[![CI](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml/badge.svg)](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/wewe29/ai-prompt-enhancer)](https://github.com/wewe29/ai-prompt-enhancer/releases)

> 📖 **[中文文档 →](./README.md)**

PromptCraft is a local AI prompt enhancer for Windows 10/11. It analyses your raw requirement and any reference materials you attach, asks up to three rounds of clarifying questions when the input is ambiguous, and then calls your own API to generate an editable main prompt, a list of changes versus the original, and 0–5 optional supplementary suggestions. When the model returns a structurally invalid result, the app keeps a deliverable anyway (partial delivery / original-prompt fallback).

The project is in the `0.3.x` iteration phase. Issues and improvement suggestions are welcome.

## For first-time users and contributors

This is the author's first public open-source release. The codebase, UI and documentation are still rough around the edges — your patience is appreciated. Please download, try it out, and report the problems, rough edges, and features you would like improved.

**Effect statement (formal evaluation, August 2026, 60 three-tier ambiguity samples × 4 target models)**: by the release-gate rule, this round has not yet met the bar for "publishing quantitative effect numbers", so we only describe it qualitatively. The enhancer helps **tasks with severely missing information** significantly (it fills in completeness, relevance, and structure). For **tasks that are already clear or only moderately ambiguous**, the gain is limited, and a small fraction of clear prompts actually regress because the enhancer over-edits them. We recommend using it when the task information is insufficient and you need clarification or context completion, and using the original directly when the information is already complete. The full evaluation report (broken down by ambiguity level / scenario / target model, plus a list of failed samples) is regenerated under `evaluation/results/` after every run.

Prefer filing an issue on GitHub so other users can see and verify it. You can also email [3986351310@qq.com](mailto:3986351310@qq.com). When sending feedback, please remove any API keys, passwords, proprietary source, or other sensitive material.

## Features

- Custom model list (configured on the settings page, selected from a dropdown on the enhancer page).
- Task-type recognition and differentiated enhancement (code / creative / writing / Q&A explanation / data analysis / translation / other).
- Streaming generation, stop, automatic retry, and up to three rounds of clarification.
- Side-by-side view of the original and enhanced prompt with per-item accept / reject.
- Pre-send quick check (local, offline, free of cost).
- One-click persona presets (casual AI user / student / office worker / programmer).
- Manual edit, undo, redo (`Ctrl+Z` / `Ctrl+Shift+Z` · `Ctrl+Y`), quick send (`Ctrl+Enter`), and close (`Esc`).
- 0–5 optional supplementary suggestions.
- Enhancement-level judgement (no meaningful change / light enhancement / clarification needed) plus task-aware pre-send quick check.
- Delivery-degradation guarantee: on a structurally invalid model response, fall back to partial delivery or the original prompt so input is never lost.
- Local text extraction from TXT, code, text-layer PDF and DOCX attachments.
- API key stored in the Windows credential manager.
- SQLCipher-encrypted local history with import / export and clean-up.
- Cost estimation, soft budget reminders, and hard budget limits.
- "Copy and open" to Doubao, DeepSeek, Qwen, MiniMax, or any custom page.

## Product boundaries

- PromptCraft only enhances prompts. It does not generate the final answer to the underlying question.
- The application never takes control of a target model's web page and never pastes or sends content on your behalf.
- The current version does not support screenshot OCR, scanned PDF, Excel, PowerPoint, archives, or project folders.
- You supply your own API key, top up your account, and bear the model call cost.
- PromptCraft does not search the web and does not fact-check the prompt content.

## Download and usage

Download the latest Windows portable ZIP from [GitHub Releases](https://github.com/wewe29/ai-prompt-enhancer/releases). Unzip the entire archive and run `PromptCraft.exe`.

The release is not code-signed, so Windows SmartScreen may show a security prompt on first launch. Only download from this repository's Release page and verify the published SHA-256 hashes.

Zero-to-running instructions: see [`PromptCraft 使用指南`](./PromptCraft使用指南.md) (Chinese — the same steps apply to English users, just substitute your own API account and language).

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

The output is written to `release/`. To ship a WebView2 bootstrapper with the portable bundle, drop Microsoft's `MicrosoftEdgeWebview2Setup.exe` into `release-assets/`. That binary is intentionally not committed to the source repository.

## Data and privacy

Your prompt, in-app context, and extracted attachment text are sent to the API endpoint you configured. The API key is never written to the front-end, database, ordinary logs, or export bundles. The application collects no telemetry and uploads no crash reports.

See [PRIVACY.md](PRIVACY.md) for the full statement. For security issues, read [SECURITY.md](SECURITY.md) — never post keys or sensitive material to a public issue.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request. Version history lives in [CHANGELOG.md](CHANGELOG.md). Third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

Copyright (C) 2026 wewe29

This project is licensed under the [GNU General Public License v3.0 only](LICENSE). Distributing a modified version requires that you honour the source and licence obligations of GPL-3.0. Third-party components remain under their respective licences.
