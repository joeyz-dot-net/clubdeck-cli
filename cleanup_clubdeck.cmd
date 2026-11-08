@echo off
rem Stop stale sessions/audio bridges of this project (per-tool scope).
rem Usage: cleanup_clubdeck.cmd [all|tui|orphans]
setlocal
set "SCOPE=%~1"
if "%SCOPE%"=="" set "SCOPE=all"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup_clubdeck.ps1" -Scope "%SCOPE%"
exit /b %errorlevel%
endlocal
