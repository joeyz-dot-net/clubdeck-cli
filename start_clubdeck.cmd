@echo off
setlocal EnableExtensions

chcp 65001 >nul
title ClubDeck CLI - Real-time Transcription

set "REPO=%~dp0"
set "PYTHON="

if defined CLUBDECK_PYTHON if exist "%CLUBDECK_PYTHON%" set "PYTHON=%CLUBDECK_PYTHON%"
rem Unified project venv .venv (portable); CLUBDECK_PYTHON overrides it.
if not defined PYTHON if exist "%REPO%.venv\Scripts\python.exe" set "PYTHON=%REPO%.venv\Scripts\python.exe"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if defined PYTHON goto run_python
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%REPO%runtime_bootstrap.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%REPO%runtime_bootstrap.py" %*
  exit /b %ERRORLEVEL%
)

echo [FAIL] Python runtime not found. Set CLUBDECK_PYTHON or run setup_venv.cmd to create .venv.
pause
exit /b 2

:run_python
"%PYTHON%" "%REPO%runtime_bootstrap.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo [FAIL] ClubDeck exited with code %EXIT_CODE%.
  pause
)
endlocal & exit /b %EXIT_CODE%
