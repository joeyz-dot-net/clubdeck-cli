@echo off
rem Create the repo-local .venv and install real-time transcription dependencies.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_venv.ps1"
exit /b %ERRORLEVEL%
