# PromptCraft 0.3.0 — Minimal-intervention enhancement with trustworthy evaluation

v0.3.0 introduces an explicit **enhancement-level** judgement (none / light / clarify) and a **task-aware pre-send quick check**, backed by a formal 60-sample, three-tier ambiguity evaluation that includes a release-gate rule before any quantitative claim is published.

## Download

Main asset:

- **`PromptCraft-0.3.0-windows-x64-portable.zip`** — Windows 10/11 64-bit portable build. Unzip the entire archive and run `PromptCraft.exe`. No Python, Node.js or any developer tool required.

Optional extras:

- `MicrosoftEdgeWebView2Setup.exe` — Microsoft's official WebView2 runtime bootstrapper. When the portable bundle ships with it in the same folder, the app installs WebView2 silently on first launch. If your system already ships WebView2 (e.g. Windows 11 23H2 or later), you can ignore this file.
- `README.txt` — short note about the asset folder.

## SHA-256 verification

```
a34bad91dd9364823058976df23493a0c67dc936df8ac20495544ae0c491a333  PromptCraft-0.3.0-windows-x64-portable.zip
e99838c51bb3379b244654aa77e33032d42fc2b5d224c5babce432d9fd3dcb28  MicrosoftEdgeWebView2Setup.exe
```

PowerShell verification example:

```powershell
Get-FileHash .\PromptCraft-0.3.0-windows-x64-portable.zip -Algorithm SHA256
Get-FileHash .\MicrosoftEdgeWebView2Setup.exe -Algorithm SHA256
```

## Important safety notes

- This release is **not code-signed**. Windows SmartScreen will show a security warning the first time you launch it.
- Download only from this repository's Release page and verify the SHA-256 hashes above.
- The application sends no telemetry and uploads no crash reports. See [PRIVACY.md](https://github.com/wewe29/ai-prompt-enhancer/blob/main/PRIVACY.md) for the full statement.

## What's new since 0.2.1

### Minimal-intervention enhancement

- **System prompt v2.1.0** — the model first judges the enhancement level (`none` / `light` / `clarify`); the supplementary suggestion cap is loosened to 0–5 items (no longer required to be exactly 5).
- **Task-aware pre-send quick check** — runs locally, free of cost, separately for each of the seven task categories (code / creative / writing / Q&A / data analysis / translation / other) and returns up to three prompts.
- **Read-only summary in the result area** — enhancement level, length delta, change summary (derived from the `changes` field) and fact source.
- **Accept / reject rebuild rules** — only the first exact match is replaced; when the anchor is missing the app now tells you "the original has been edited" instead of silently mutating the prompt.

### Trustworthy evaluation (v0.3.0 baseline)

- **60 formal samples** — `clear` / `medium` / `severe` ambiguity tiers × 6 scenarios, each carrying `must_preserve` / `must_not_add` / `expected_behavior` constraints.
- **A/B/C control groups** (original / length-filler / enhanced) enabled by default; `--repeats N` reruns the same comparison; each comparison records latency, estimated tokens and error code.
- **Decoupled targets and judges** — the target model and the judge / enhancer are no longer co-deployed; a second judge is enabled by default and consistency is reported.
- **Report upgrades** — per-ambiguity-tier breakdown, C−B and B−A side-by-side tables, a "do not use for product claims" release-gate banner, and a `release_gate.py` checker.
- **Formal evaluation (60 × 4) conclusion (qualitative)** — the enhancer helps severely under-specified tasks significantly; for medium / clear tasks the gain is limited, and a small fraction of clear prompts actually regress. **Per the release-gate rule, this README only carries the qualitative statement**; no quantitative effect number is published yet.

## Features

- DeepSeek Chat and `deepseek-v4-flash` model switching.
- Streaming generation, stop, automatic retry, and up to three rounds of clarification.
- Side-by-side view of the original and enhanced prompt with per-item accept / reject.
- Pre-send quick check (local, offline, free of cost).
- One-click persona presets (casual AI user / student / office worker / programmer).
- Manual edit, undo, redo, quick send (`Ctrl+Enter`), close (`Esc`).
- 0–5 optional supplementary suggestions.
- Enhancement-level judgement and task-aware pre-send check.
- Delivery-degradation guarantee: partial delivery or original-prompt fallback when the model response is structurally invalid.
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

## Feedback

- Prefer filing an issue on GitHub so other users can see and verify it.
- You can also email `3986351310@qq.com`. **Strip out any API keys, passwords, proprietary source or other sensitive material** before sending.
- See [`PromptCraft 使用指南`](https://github.com/wewe29/ai-prompt-enhancer/blob/main/PromptCraft使用指南.md) and the [`README`](https://github.com/wewe29/ai-prompt-enhancer) for the full guides.

## License

Copyright (C) 2026 wewe29

This project is licensed under the [GNU General Public License v3.0 only](https://github.com/wewe29/ai-prompt-enhancer/blob/main/LICENSE).
