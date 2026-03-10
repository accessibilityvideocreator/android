@echo off
title TTS Generator — Accessibility Video Creator
cd /d "%~dp0"

REM ── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found.
    echo  Install Python 3 from https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo Python found:
python --version
echo.

REM ── Launch web app (opens automatically in your browser) ──────────────────
echo Starting TTS Generator web interface...
echo Your browser will open automatically at http://127.0.0.1:8765
echo.
echo Press Ctrl+C here (or close this window) to stop the server.
echo.
python tts_web.py
if errorlevel 1 (
    echo.
    echo  The server exited with an error. Details above.
    pause
)
