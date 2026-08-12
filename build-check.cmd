@echo off
rem build-check.cmd [test] - runs the full 7-stage verification suite.
rem   test   (default) run all checks: npm test, npm build, pytest, fmt, cargo test, clippy, workspace sanity.
rem Direct invocation of scripts\build-check.ps1 -Test keeps the old single-step cargo test behavior.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-check.ps1"
if errorlevel 1 exit /b %errorlevel%
