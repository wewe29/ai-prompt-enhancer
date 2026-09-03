# PromptCraft 0.3.1 — Release stability

v0.3.1 is a **release-stability** iteration: no new product features. It hardens the test/build toolchain (unified writable temp directories, environment-vs-code error classification), moves portable ZIP archives out of the source repository (releases are now uploaded straight from a local build via the GitHub CLI), and adds an automated end-to-end smoke test for the portable build.

## Download

Main asset:

- **`PromptCraft-0.3.1-windows-x64-portable.zip`** — Windows 10/11 64-bit portable build. Unzip the entire archive and run `PromptCraft.exe`. No Python, Node.js or any developer tool required.

Optional extras:

- `MicrosoftEdgeWebView2Setup.exe` — Microsoft's official WebView2 runtime bootstrapper. When the portable bundle ships with it in the same folder, the app installs WebView2 silently on first launch. If your system already ships WebView2 (e.g. Windows 11 23H2 or later), you can ignore this file.
- `README.txt` — short note about the asset folder.

## SHA-256 verification

```
4da10fe5322ffd152144a53da782d458db6f7e037608f6ea8065f302e305ef98  PromptCraft-0.3.1-windows-x64-portable.zip
e99838c51bb3379b244654aa77e33032d42fc2b5d224c5babce432d9fd3dcb28  MicrosoftEdgeWebView2Setup.exe
```

PowerShell verification example:

```powershell
Get-FileHash .\PromptCraft-0.3.1-windows-x64-portable.zip -Algorithm SHA256
Get-FileHash .\MicrosoftEdgeWebView2Setup.exe -Algorithm SHA256
```

## Important safety notes

- This release is **not code-signed**. Windows SmartScreen will show a security warning the first time you launch it.
- Download only from this repository's Release page and verify the SHA-256 hashes above.
- The application sends no telemetry and uploads no crash reports. See [PRIVACY.md](https://github.com/wewe29/ai-prompt-enhancer/blob/main/PRIVACY.md) for the full statement.

## What's new since 0.3.0

### Release stability (tooling)

- **Portable ZIPs are no longer committed to the repository.** `.gitignore` ignores `release/*.zip` again; releases are published from a local build with `gh release create`. The v0.3.0 bootstrap workflow is frozen (v0.3.0-only).
- **Unified temp directories** under a flat `%TEMP%\PromptCraft-*` prefix: `PromptCraft-vite-<pid>` (cleaned on process exit), `PromptCraft-pytest-<pid>` (cleaned at session end), `PromptCraft-cargo-target` (persisted Rust incremental-build cache — renaming it means the first build after upgrading recompiles Rust from scratch).
- **`evaluation/conftest.py`** redirects plain `python -m pytest evaluation -q` runs to the same `%TEMP%\PromptCraft-pytest-<pid>` baseline and cleans up afterwards.
- **`build-check.ps1`**: new `-Clean` switch to wipe every `PromptCraft-*` temp dir; stale-dir sweep on start; pytest basetemp removed in a `finally` block.
- **Error classification**: an environment preflight runs before the 7 verification stages; exit codes are now `0` = passed, `1` = code failure, `2` = environment failure, with `(CODE|ENVIRONMENT)` annotations and a summary line.
- **OpenSSL probing** checks a list of common install locations (verifying `include\openssl\ssl.h`) and, when nothing is found, prints every probed path plus exact `winget` / `setx` fix commands.
- **`build-portable.ps1`** verifies version consistency across `package.json` / `Cargo.toml` / `tauri.conf.json` / `Cargo.lock`, checks the built exe's `ProductVersion`, rebuilds the staging folder from scratch, and writes `release-assets/SHA256-zip.txt` automatically.
- **`scripts/smoke-portable.ps1`** automates an end-to-end smoke test of the portable build: fresh extraction, credential-manager backup/restore, streaming generation, stop, regenerate, clipboard copy, history save/restore, original-prompt retention on API failure, and a temp-directory residue check.
- **`evaluation/requirements.txt` is fully pinned** (including transitive dependencies) to the versions verified in the local venv. Previously floating ranges (`openai>=1.40.0` etc.) made CI install the newest versions on every run; a breaking upstream release kept CI's evaluation-test step red since 2026-08-14 while the local venv passed.

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
