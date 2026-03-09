@echo off
title TTS Generator — Accessibility Video Creator
cd /d "%~dp0"

REM ── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3 from https://python.org and try again.
    pause
    exit /b 1
)

REM ── Launch the app ─────────────────────────────────────────────────────────
python tts_generator.py
