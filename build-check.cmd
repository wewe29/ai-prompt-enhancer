@echo off
if /I "%~1"=="test" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-check.ps1" -Test
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-check.ps1"
)
