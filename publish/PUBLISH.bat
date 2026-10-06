@echo off
rem Double-click: publish a new version to GitHub (Release with a permanent download link).
rem See README.md next to this file.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1"
echo.
pause
