@echo off
rem build-check.cmd - runs environment preflight + the full 7-stage verification suite.
rem   (no args) preflight, then: npm test, npm build, pytest, fmt, cargo test, clippy, workspace sanity.
rem Exit codes: 0 = passed, 1 = code failure, 2 = environment failure.
rem scripts\build-check.ps1 -Clean  clears all %%TEMP%%\PromptCraft-* temp dirs.
rem scripts\build-check.ps1 -Test   keeps the old single-step cargo test behavior.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-check.ps1"
if errorlevel 1 exit /b %errorlevel%
