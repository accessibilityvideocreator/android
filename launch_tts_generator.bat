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

REM ── Check tkinter (built into Python but sometimes missing) ───────────────
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo  ERROR: tkinter is not available in your Python install.
    echo  On Windows this is rare - try reinstalling Python from python.org
    echo  and check "tcl/tk and IDLE" during the install.
    echo.
    pause
    exit /b 1
)

REM ── pyttsx3 is optional (for offline TTS) — app handles missing install ──
REM    python -m pip install pyttsx3

REM ── Launch the app ─────────────────────────────────────────────────────────
echo Launching TTS Generator...
python tts_generator.py
if errorlevel 1 (
    echo.
    echo  The app exited with an error. Details above.
    pause
)
