# PromptCraft

[![CI](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml/badge.svg)](https://github.com/wewe29/ai-prompt-enhancer/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/wewe29/ai-prompt-enhancer)](https://github.com/wewe29/ai-prompt-enhancer/releases)

> 📖 **[中文文档 →](./README.md)**

PromptCraft is a local AI prompt enhancer for Windows 10/11. It analyses your raw requirement and any reference materials you attach, asks up to three rounds of clarifying questions when the input is ambiguous, and then calls your own API to generate an editable main prompt, a list of changes versus the original, and 0–5 optional supplementary suggestions. When the model returns a structurally invalid result, the app keeps a deliverable anyway (partial delivery / original-prompt fallback).

The project is in the `0.3.x` iteration phase. Issues and improvement suggestions are welcome.

## For first-time users and contributors

This is the author's first public open-source release. The codebase, UI and documentation are still rough around the edges — your patience is appreciated. Please download, try it out, and report the problems, rough edges, and features you would like improved.

**Effect statement (v0.3.2 re-evaluation, September 2026: 60 three-tier ambiguity samples x 4 target models x 3 repeats + 48-unit blind human review)**: by the release-gate rule, this round still does not meet the bar for publishing quantitative effect numbers (quality gain on severely/moderately ambiguous tasks, regression rate on clear tasks, expansion ratio, and dual-judge agreement all miss their thresholds), so we only describe it qualitatively. Enhanced answers are usually longer and more structured, but the **blind human review found no quality advantage** (0 of 48 blind-reviewed units favored the enhanced answer; 43.8% were judged close and 56.3% favored the original). The inflated win rate in the automated evaluation mainly reflects an LLM-judge bias toward longer answers. **For tasks you have already written clearly, prefer the original prompt directly** (the over-editing risk on clear prompts is confirmed by both evaluation channels); for tasks with severely missing information you may try enhancement, but review the changes item by item before adopting them. Full report: docs/PromptCraft-v0.3.2-评测报告.md (Chinese).

Prefer filing an issue on GitHub so other users can see and verify it. You can also email [3986351310@qq.com](mailto:3986351310@qq.com). When sending feedback, please remove any API keys, passwords, proprietary source, or other sensitive material.

**Enhancement-capability verification (v0.3.5, 60 samples on the DeepSeek-V4.1-Flash base model)**: after switching the base model to V4.1-Flash, all 60 three-tier samples were re-run and **all 60 level judgements were correct** — all 20 severely ambiguous samples were **turned into clarifications** (listing what is missing plus a temporary plan built on explicitly stated assumptions, without inventing facts); 17 moderately ambiguous samples received light completion and 3 were judged to need no change; of the 20 clear samples, **11 were returned unchanged**, with only light formatting applied. Zero cases of instruction overreach. The takeaway: **the enhancer knows when it should not act.** If you have already spelled out the goal, background, constraints and output format, just use your original prompt.

> Enhancement and review were performed by two *different* models (not self-scoring), but **no human blind review was carried out**. Treat this as an indication, not a human judgement, and do not compare it directly with the v0.3.2 conclusions (both the base model and the review method changed). See [docs/PromptCraft-增强能力验证报告-v0.3.5.md](docs/PromptCraft-增强能力验证报告-v0.3.5.md) and the review rules [docs/PromptCraft-增强器评审规则-单模型版.md](docs/PromptCraft-增强器评审规则-单模型版.md) (both Chinese).

## v0.3.4 / v0.3.5 security and data-reliability hardening

These two iterations hardened four attack surfaces — import, credentials, attachments and local data — based on the empirical findings of a single-model code review (see [docs/PromptCraft-v0.3.4-评审报告.md](docs/PromptCraft-v0.3.4-评审报告.md), Chinese):

- **An imported data package can never overwrite your API key.** The `apiKey` field inside `provider.json` is now explicitly ignored during deserialization and cleared again inside the import flow. Previously, a ZIP from an untrusted source could silently replace your DeepSeek key with an attacker's (reproduced in testing, now fixed), redirecting both your prompt content and your billing.
- **Pinning actually works.** Pinned records are **never** removed by the 90-day retention sweep or by capacity cleanup (the history page has a pin button; pinned items sort first and are highlighted).
- **The history capacity limit is configurable.** The settings page adds "History capacity limit (MB)" (default 64 MB, range 1–1024). Once exceeded, the **oldest unpinned** records are removed one by one until the limit is met.
- **ZIP import hardened**: path traversal blocked, file count and total size limited, per-entry uncompressed hard cap of 8 MB (zip-bomb defence).
- **Attachments fail safely**: empty, oversized, corrupt PDF/DOCX and binary-disguised-as-text files all produce an error instead of a crash. Attachments are read in place and **never create temporary files**.
- **Error messages never leak credentials**: server-echoed content passes through credential redaction, so your API key cannot appear in any error text.
- **Tests no longer pollute your credential store**: tests use isolated temp directories and credential service names and clean up afterwards (previously every `cargo test` run appended a batch of `PromptCraftTest-*` entries to the Windows credential manager).

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
- Result view shows a delivery badge, user-provided facts, risk flags with required protection, and switchable prompt candidates (1-3).
- Change list supports per-item or batch accept / reject; partial deliveries list missing fields; clarification shows current and remaining rounds.
- History records support **pinning** (pinned items are never auto-cleaned) and a **configurable capacity limit**.
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
