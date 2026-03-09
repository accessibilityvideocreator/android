#!/usr/bin/env python3
"""
TTS Generator — Accessibility Video Creator
--------------------------------------------
Desktop app for generating TTS audio and optionally making a scrolling video.

Services:
  • ElevenLabs        — cloud, high quality, needs API key
  • Google Cloud TTS  — cloud, Neural2 voices, needs API key (auto-chunked)
  • System TTS        — offline, uses Windows built-in voices, no API key needed
                        (requires:  pip install pyttsx3)

Requirements (all optional — app tells you what to install):
    pip install pyttsx3      ← for System TTS (offline)

Built-in to Python 3:  tkinter, json, threading, subprocess, pathlib, urllib
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import base64
import pathlib
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

# ─── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = pathlib.Path(__file__).parent
CONFIG_FILE   = SCRIPT_DIR / "tts_generator_config.json"
NARRATION_TXT = SCRIPT_DIR / "example" / "HOW_IT_WORKS_narration.txt"
SCREENSHOT_PNG = SCRIPT_DIR / "HOW_IT_WORKS_screenshot.png"
MAKE_VIDEO_PY  = SCRIPT_DIR / "make_doc_video.py"

DEFAULT_CFG = {
    "service":        "system",           # default to offline — no setup needed
    "el_api_key":     "",
    "el_voice_id":    "21m00Tcm4TlvDq8ikWAM",   # Rachel
    "google_api_key": "",
    "google_voice":   "en-US-Standard-B",
    "output_dir":     str(pathlib.Path.home() / "Downloads"),
}

def load_cfg():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                d = json.load(f)
            cfg = dict(DEFAULT_CFG)
            cfg.update(d)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CFG)

def save_cfg(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Could not save config: {e}")


# ─── pyttsx3 availability check ────────────────────────────────────────────────
def _pyttsx3_available() -> bool:
    try:
        import pyttsx3
        return True
    except ImportError:
        return False


def _install_pyttsx3(log_fn=None):
    """pip install pyttsx3 in a subprocess and return True on success."""
    if log_fn: log_fn("Installing pyttsx3 via pip…")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyttsx3"],
        capture_output=True, text=True
    )
    if log_fn:
        for line in result.stdout.splitlines():
            log_fn(f"  {line}")
        for line in result.stderr.splitlines():
            log_fn(f"  {line}")
    return result.returncode == 0


# ─── TTS functions ─────────────────────────────────────────────────────────────
def _sanitize_key(key: str) -> str:
    return "".join(c for c in key if 0x20 <= ord(c) <= 0x7E).strip()


def synthesize_system_tts(text: str, out_path: str, log_fn=None):
    """Use Windows built-in voices via pyttsx3. Saves WAV to out_path."""
    try:
        import pyttsx3
    except ImportError:
        raise RuntimeError(
            "pyttsx3 is not installed.\n"
            "Click 'Install pyttsx3' in the settings panel, then try again."
        )

    if log_fn: log_fn("Using System TTS (offline, Windows voices)…")
    engine = pyttsx3.init()

    # List available voices in the log
    voices = engine.getProperty("voices")
    if log_fn: log_fn(f"  Found {len(voices)} system voice(s)")
    for v in voices[:5]:
        if log_fn: log_fn(f"    • {v.name}")

    engine.save_to_file(text, out_path)
    engine.runAndWait()

    if not pathlib.Path(out_path).exists() or pathlib.Path(out_path).stat().st_size < 100:
        raise RuntimeError(
            "System TTS produced no audio. "
            "Make sure Windows TTS voices are installed (Settings → Time & Language → Speech)."
        )
    if log_fn: log_fn(f"  ✓ WAV saved: {out_path}")


def _chunk_text(text: str, max_chars: int = 4999) -> list:
    """
    Split text into chunks ≤ max_chars, breaking on sentence boundaries.
    Google TTS has a 5000-byte limit per request; we use 4800 to be safe.
    """
    # Split on sentence-ending punctuation followed by whitespace
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, current = [], ""
    for sentence in sentences:
        if not sentence:
            continue
        # If a single sentence is too long, split on commas/semicolons
        if len(sentence) > max_chars:
            for part in re.split(r'(?<=[,;:])\s+', sentence):
                if len(current) + len(part) + 1 <= max_chars:
                    current = (current + " " + part).strip()
                else:
                    if current:
                        chunks.append(current)
                    current = part
        elif len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def synthesize_elevenlabs(text: str, api_key: str, voice_id: str, out_path: str,
                           log_fn=None):
    """Call ElevenLabs API and write MP3 to out_path."""
    key = _sanitize_key(api_key)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = json.dumps({
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Accept", "audio/mpeg")
    req.add_header("Content-Type", "application/json")

    if log_fn: log_fn(f"Calling ElevenLabs API  ({len(text):,} chars)…")
    try:
        with urllib.request.urlopen(req) as resp:
            audio_bytes = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ElevenLabs HTTP {e.code}: {body[:300]}")

    if log_fn: log_fn(f"  Received {len(audio_bytes):,} bytes of audio")
    with open(out_path, "wb") as f:
        f.write(audio_bytes)


def _google_synthesize_chunk(chunk: str, api_key: str, lang_code: str,
                              voice_name: str) -> bytes:
    """Single Google TTS API call for one chunk. Returns MP3 bytes."""
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    payload = json.dumps({
        "input": {"text": chunk},
        "voice": {"languageCode": lang_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3"}
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google TTS HTTP {e.code}: {body[:300]}")
    return base64.b64decode(data["audioContent"])


def synthesize_google(text: str, api_key: str, voice_name: str, out_path: str,
                      log_fn=None):
    """
    Call Google Cloud TTS API, auto-chunking text to stay under 5000 chars.
    Writes MP3 to out_path.
    """
    lang_code = "-".join(voice_name.split("-")[:2])   # "en-US-Neural2-F" → "en-US"
    chunks = _chunk_text(text, max_chars=4800)

    if log_fn:
        log_fn(f"Google TTS: {len(text):,} chars split into {len(chunks)} chunk(s)")

    all_mp3 = bytearray()
    for i, chunk in enumerate(chunks, 1):
        if log_fn: log_fn(f"  Chunk {i}/{len(chunks)}  ({len(chunk):,} chars)…")
        mp3_bytes = _google_synthesize_chunk(chunk, api_key, lang_code, voice_name)
        all_mp3.extend(mp3_bytes)
        if log_fn: log_fn(f"    → {len(mp3_bytes):,} bytes")

    if log_fn: log_fn(f"  Total audio: {len(all_mp3):,} bytes")
    with open(out_path, "wb") as f:
        f.write(all_mp3)


# ─── Colours & fonts ───────────────────────────────────────────────────────────
BG          = "#0d1117"
BG2         = "#161b22"
BORDER      = "#30363d"
FG          = "#c9d1d9"
FG_BRIGHT   = "#e6edf3"
ACCENT_GRN  = "#3fb950"
ACCENT_RED  = "#f85149"
ACCENT_ORG  = "#d29922"
FONT_UI     = ("Segoe UI", 10)
FONT_MONO   = ("Consolas", 9)
FONT_H1     = ("Segoe UI", 13, "bold")


# ─── Main window ───────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TTS Generator — Accessibility Video Creator")
        self.configure(bg=BG)
        self.geometry("860x820")
        self.minsize(700, 620)
        self.resizable(True, True)

        self.cfg = load_cfg()
        self._last_audio_path = None
        self._build_ui()
        self._load_narration_if_exists()

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg=BG, padx=16, pady=10)
        top.pack(fill="x")
        tk.Label(top, text="TTS Generator", font=FONT_H1,
                 bg=BG, fg=FG_BRIGHT).pack(side="left")
        tk.Label(top, text=" · Accessibility Video Creator",
                 font=("Segoe UI", 10), bg=BG, fg="#8b949e").pack(side="left")

        # ── Service selector ─────────────────────────────────────────────────
        svc_frame = tk.LabelFrame(self, text=" TTS Service ", font=FONT_UI,
                                  bg=BG, fg="#8b949e", bd=1, relief="solid",
                                  padx=12, pady=10)
        svc_frame.pack(fill="x", padx=16, pady=(0, 8))

        self.svc_var = tk.StringVar(value=self.cfg.get("service", "system"))
        services = [
            ("system",      "🖥  System TTS  (offline, free, no API key)"),
            ("elevenlabs",  "🎙  ElevenLabs  (cloud, premium quality)"),
            ("google",      "☁  Google Cloud TTS  (cloud, Neural2 voices)"),
        ]
        for val, label in services:
            tk.Radiobutton(svc_frame, text=label, variable=self.svc_var,
                           value=val, command=self._on_service_change,
                           bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                           font=FONT_UI).pack(anchor="w", padx=4, pady=1)

        # ── Settings panel ───────────────────────────────────────────────────
        self.settings_frame = tk.LabelFrame(self, text=" Settings ", font=FONT_UI,
                                             bg=BG, fg="#8b949e", bd=1, relief="solid",
                                             padx=12, pady=10)
        self.settings_frame.pack(fill="x", padx=16, pady=(0, 8))
        self._build_settings()

        # ── Text input ───────────────────────────────────────────────────────
        txt_frame = tk.LabelFrame(self, text=" Text to Speak ", font=FONT_UI,
                                   bg=BG, fg="#8b949e", bd=1, relief="solid",
                                   padx=12, pady=8)
        txt_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        btn_row = tk.Frame(txt_frame, bg=BG)
        btn_row.pack(fill="x", pady=(0, 6))
        self._btn(btn_row, "📂 Load file…",
                  self._load_file, "#21262d", FG).pack(side="left")
        self._btn(btn_row, "📄 Load narration.txt",
                  self._load_narration, "#21262d", FG).pack(side="left", padx=6)
        self._btn(btn_row, "🗑 Clear",
                  self._clear_text, "#21262d", ACCENT_RED).pack(side="right")
        self.char_lbl = tk.Label(btn_row, text="0 chars",
                                  font=("Segoe UI", 9), bg=BG, fg="#8b949e")
        self.char_lbl.pack(side="right", padx=8)

        self.text_box = scrolledtext.ScrolledText(
            txt_frame, wrap="word", bg=BG2, fg=FG, insertbackground=FG,
            font=FONT_MONO, relief="flat", bd=0, height=12,
            selectbackground="#264f78"
        )
        self.text_box.pack(fill="both", expand=True)
        self.text_box.bind("<KeyRelease>", self._on_text_change)

        # ── Output settings ──────────────────────────────────────────────────
        out_frame = tk.LabelFrame(self, text=" Output ", font=FONT_UI,
                                   bg=BG, fg="#8b949e", bd=1, relief="solid",
                                   padx=12, pady=8)
        out_frame.pack(fill="x", padx=16, pady=(0, 8))

        r1 = tk.Frame(out_frame, bg=BG)
        r1.pack(fill="x")
        tk.Label(r1, text="Save to folder:", font=FONT_UI,
                 bg=BG, fg=FG).pack(side="left")
        self.out_dir_var = tk.StringVar(value=self.cfg.get("output_dir",
                                         str(pathlib.Path.home() / "Downloads")))
        tk.Entry(r1, textvariable=self.out_dir_var, bg=BG2, fg=FG,
                 insertbackground=FG, font=FONT_MONO, relief="flat",
                 bd=1).pack(side="left", fill="x", expand=True, padx=8)
        self._btn(r1, "Browse…", self._browse_output,
                  "#21262d", FG).pack(side="left")

        r2 = tk.Frame(out_frame, bg=BG)
        r2.pack(fill="x", pady=(8, 0))
        tk.Label(r2, text="Screenshot PNG (for video):",
                 font=FONT_UI, bg=BG, fg=FG).pack(side="left")
        self.png_path_var = tk.StringVar(value=str(SCREENSHOT_PNG))
        tk.Entry(r2, textvariable=self.png_path_var, bg=BG2, fg=FG,
                 insertbackground=FG, font=FONT_MONO, relief="flat",
                 bd=1).pack(side="left", fill="x", expand=True, padx=8)
        self._btn(r2, "Browse…", self._browse_png,
                  "#21262d", FG).pack(side="left")

        # ── Action buttons ───────────────────────────────────────────────────
        act_frame = tk.Frame(self, bg=BG, padx=16, pady=8)
        act_frame.pack(fill="x")

        self.gen_btn = self._btn(
            act_frame, "🎙  Generate Audio", self._on_generate,
            "#1f6feb", FG_BRIGHT, font=("Segoe UI", 11, "bold"), padx=20, pady=8)
        self.gen_btn.pack(side="left")

        self.vid_btn = self._btn(
            act_frame, "🎬  Generate Video", self._on_generate_video,
            "#238636", FG_BRIGHT, font=("Segoe UI", 11, "bold"), padx=20, pady=8)
        self.vid_btn.pack(side="left", padx=8)

        self.open_btn = self._btn(
            act_frame, "▶  Open Audio", self._open_last_audio,
            "#21262d", FG, font=FONT_UI, padx=12, pady=8)
        self.open_btn.pack(side="left")
        self.open_btn.config(state="disabled")

        self.save_lbl = tk.Label(act_frame, text="",
                                  font=("Segoe UI", 9), bg=BG, fg=ACCENT_GRN)
        self.save_lbl.pack(side="left", padx=8)

        # ── Log panel ────────────────────────────────────────────────────────
        log_frame = tk.LabelFrame(self, text=" Log ", font=FONT_UI,
                                   bg=BG, fg="#8b949e", bd=1, relief="solid",
                                   padx=8, pady=6)
        log_frame.pack(fill="x", padx=16, pady=(0, 12))

        self.log_box = scrolledtext.ScrolledText(
            log_frame, wrap="word", bg="#010409", fg="#8b949e",
            font=FONT_MONO, height=6, relief="flat", bd=0,
            state="disabled"
        )
        self.log_box.pack(fill="both")

        self._on_service_change()

    # ── Settings panel content ────────────────────────────────────────────────
    def _build_settings(self):
        for w in self.settings_frame.winfo_children():
            w.destroy()

        svc = self.svc_var.get()

        if svc == "system":
            has_pyttsx3 = _pyttsx3_available()
            if has_pyttsx3:
                tk.Label(self.settings_frame,
                         text="✅  pyttsx3 is installed — ready to use offline.",
                         font=FONT_UI, bg=BG, fg=ACCENT_GRN).pack(anchor="w")
                tk.Label(self.settings_frame,
                         text="Uses your Windows built-in voices (no API key, no internet, no character limit).",
                         font=("Segoe UI", 9), bg=BG, fg="#8b949e").pack(anchor="w")
                tk.Label(self.settings_frame,
                         text="To change voice or speed: Settings → Time & Language → Speech on Windows.",
                         font=("Segoe UI", 9), bg=BG, fg="#8b949e").pack(anchor="w", pady=(2,0))
            else:
                tk.Label(self.settings_frame,
                         text="⚠  pyttsx3 is not installed.",
                         font=FONT_UI, bg=BG, fg=ACCENT_ORG).pack(anchor="w")
                tk.Label(self.settings_frame,
                         text='Click "Install pyttsx3" below, or run:  pip install pyttsx3',
                         font=("Segoe UI", 9), bg=BG, fg="#8b949e").pack(anchor="w", pady=(2, 6))
                self._btn(self.settings_frame, "⬇  Install pyttsx3 now",
                          self._install_pyttsx3_clicked,
                          "#1f6feb", FG_BRIGHT, padx=14, pady=6).pack(anchor="w")

        elif svc == "elevenlabs":
            self._lbl_entry(self.settings_frame, "API Key (xi-api-key):",
                            self.cfg.get("el_api_key", ""), "el_api_key", show="*")
            self._lbl_entry(self.settings_frame, "Voice ID:",
                            self.cfg.get("el_voice_id", DEFAULT_CFG["el_voice_id"]),
                            "el_voice_id")
            tk.Label(self.settings_frame,
                     text="Default voice: Rachel (21m00Tcm4TlvDq8ikWAM)  ·  "
                          "Browse voices at elevenlabs.io/voice-library",
                     font=("Segoe UI", 8), bg=BG, fg="#8b949e").pack(anchor="w")

        elif svc == "google":
            self._lbl_entry(self.settings_frame, "API Key:",
                            self.cfg.get("google_api_key", ""), "google_api_key", show="*")
            self._lbl_entry(self.settings_frame, "Voice Name:",
                            self.cfg.get("google_voice", DEFAULT_CFG["google_voice"]),
                            "google_voice")
            tk.Label(self.settings_frame,
                     text="Get key at console.cloud.google.com  ·  Long texts auto-split at 4999 chars.",
                     font=("Segoe UI", 8), bg=BG, fg="#8b949e").pack(anchor="w")

    def _lbl_entry(self, parent, label, value, key, show=None):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, font=FONT_UI, bg=BG, fg=FG,
                 width=22, anchor="w").pack(side="left")
        var = tk.StringVar(value=value)
        tk.Entry(row, textvariable=var, bg=BG2, fg=FG, insertbackground=FG,
                 font=FONT_MONO, relief="flat", bd=1,
                 show=show or "").pack(side="left", fill="x", expand=True)

        def _trace(*_):
            self.cfg[key] = var.get()
            save_cfg(self.cfg)
        var.trace_add("write", _trace)

    def _btn(self, parent, text, cmd, bg, fg, font=None, padx=10, pady=4, **kw):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg=fg, activebackground=BORDER,
                      activeforeground=FG_BRIGHT, relief="flat", bd=0,
                      cursor="hand2", font=font or FONT_UI,
                      padx=padx, pady=pady, **kw)
        b.bind("<Enter>", lambda e: b.config(bg=BORDER))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    # ── Event handlers ────────────────────────────────────────────────────────
    def _on_service_change(self):
        self.cfg["service"] = self.svc_var.get()
        save_cfg(self.cfg)
        self._build_settings()

    def _on_text_change(self, _=None):
        n = len(self.text_box.get("1.0", "end-1c"))
        self.char_lbl.config(text=f"{n:,} chars")

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="Open text file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if path:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.text_box.delete("1.0", "end")
            self.text_box.insert("1.0", content)
            self._on_text_change()
            self._log(f"Loaded: {path}")

    def _load_narration(self):
        if NARRATION_TXT.exists():
            with open(NARRATION_TXT, encoding="utf-8") as f:
                content = f.read()
            self.text_box.delete("1.0", "end")
            self.text_box.insert("1.0", content)
            self._on_text_change()
            self._log(f"Loaded: {NARRATION_TXT.name}")
        else:
            messagebox.showwarning("Not found",
                f"Could not find:\n{NARRATION_TXT}\n\nPaste your text manually.")

    def _load_narration_if_exists(self):
        if NARRATION_TXT.exists():
            with open(NARRATION_TXT, encoding="utf-8") as f:
                content = f.read()
            self.text_box.insert("1.0", content)
            self._on_text_change()

    def _clear_text(self):
        self.text_box.delete("1.0", "end")
        self._on_text_change()

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.out_dir_var.set(path)
            self.cfg["output_dir"] = path
            save_cfg(self.cfg)

    def _browse_png(self):
        path = filedialog.askopenfilename(
            title="Select screenshot PNG",
            filetypes=[("PNG images", "*.png"), ("All files", "*.*")]
        )
        if path:
            self.png_path_var.set(path)

    def _open_last_audio(self):
        if self._last_audio_path and pathlib.Path(self._last_audio_path).exists():
            os.startfile(self._last_audio_path)

    def _install_pyttsx3_clicked(self):
        self.gen_btn.config(state="disabled")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self):
        ok = _install_pyttsx3(log_fn=self._log)
        if ok:
            self._log("✅ pyttsx3 installed successfully!", ACCENT_GRN)
            self.after(0, self._build_settings)   # refresh panel
        else:
            self._log("❌ Install failed. Try running:  pip install pyttsx3", ACCENT_RED)
        self.after(0, lambda: self.gen_btn.config(state="normal"))

    # ── Logging ──────────────────────────────────────────────────────────────
    def _log(self, msg: str, color=None):
        def _do():
            self.log_box.config(state="normal")
            ts = time.strftime("%H:%M:%S")
            tag = f"t{time.time_ns()}"
            self.log_box.insert("end", f"[{ts}] {msg}\n", tag)
            if color:
                self.log_box.tag_config(tag, foreground=color)
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _do)

    # ── Generate ─────────────────────────────────────────────────────────────
    def _validate_tts(self) -> str | None:
        """Return error string if TTS isn't ready, else None."""
        svc = self.svc_var.get()
        if svc == "system" and not _pyttsx3_available():
            return "pyttsx3 is not installed.\nClick 'Install pyttsx3' in the Settings panel first."
        if svc == "elevenlabs" and not self.cfg.get("el_api_key"):
            return "Please enter your ElevenLabs API key in Settings."
        if svc == "google" and not self.cfg.get("google_api_key"):
            return "Please enter your Google Cloud TTS API key in Settings."
        return None

    def _on_generate(self):
        text = self.text_box.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("No text", "Please enter some text first.")
            return
        err = self._validate_tts()
        if err:
            messagebox.showwarning("Not ready", err)
            return
        self._set_busy(True, audio_only=True)
        threading.Thread(target=self._generate_worker,
                         args=(text, False), daemon=True).start()

    def _on_generate_video(self):
        text = self.text_box.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("No text", "Please enter some text first.")
            return
        err = self._validate_tts()
        if err:
            messagebox.showwarning("Not ready", err)
            return
        png = self.png_path_var.get().strip()
        if not pathlib.Path(png).exists():
            messagebox.showwarning("PNG not found",
                f"Screenshot PNG not found:\n{png}\n\nBrowse to it using the folder icon.")
            return
        self._set_busy(True, audio_only=False)
        threading.Thread(target=self._generate_worker,
                         args=(text, True), daemon=True).start()

    def _set_busy(self, busy: bool, audio_only: bool = True):
        if busy:
            self.gen_btn.config(state="disabled",
                                text="⏳  Generating…" if audio_only else "🎙  Generating Audio…")
            self.vid_btn.config(state="disabled",
                                text="⏳  Generating…" if not audio_only else "🎬  Generate Video")
            self.save_lbl.config(text="")
        else:
            self.gen_btn.config(state="normal", text="🎙  Generate Audio")
            self.vid_btn.config(state="normal", text="🎬  Generate Video")

    def _generate_worker(self, text: str, make_video: bool):
        try:
            out_dir = pathlib.Path(self.out_dir_var.get())
            out_dir.mkdir(parents=True, exist_ok=True)
            ts  = time.strftime("%Y%m%d_%H%M%S")
            svc = self.svc_var.get()

            # System TTS outputs WAV; cloud services output MP3
            ext       = "wav" if svc == "system" else "mp3"
            audio_out = str(out_dir / f"tts_audio_{ts}.{ext}")

            if svc == "system":
                synthesize_system_tts(text, audio_out, log_fn=self._log)
            elif svc == "elevenlabs":
                synthesize_elevenlabs(
                    text, self.cfg["el_api_key"], self.cfg["el_voice_id"],
                    audio_out, log_fn=self._log)
            elif svc == "google":
                synthesize_google(
                    text, self.cfg["google_api_key"], self.cfg["google_voice"],
                    audio_out, log_fn=self._log)

            self._last_audio_path = audio_out
            size_kb = os.path.getsize(audio_out) // 1024
            self._log(f"✅ Audio saved: {audio_out}  ({size_kb} KB)", ACCENT_GRN)
            self.after(0, lambda: self.save_lbl.config(
                text=f"✓ {pathlib.Path(audio_out).name}"))
            self.after(0, lambda: self.open_btn.config(state="normal"))

            if make_video:
                self._make_video_worker(audio_out, ts)

        except Exception as e:
            self._log(f"❌ {e}", ACCENT_RED)
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
        finally:
            self.after(0, lambda: self._set_busy(False))

    def _make_video_worker(self, audio_path: str, ts: str):
        if not MAKE_VIDEO_PY.exists():
            self._log(f"⚠  make_doc_video.py not found at {MAKE_VIDEO_PY}", ACCENT_ORG)
            return
        png_path = self.png_path_var.get().strip()
        if not pathlib.Path(png_path).exists():
            self._log(f"⚠  Screenshot PNG not found: {png_path}", ACCENT_ORG)
            return

        out_dir   = pathlib.Path(self.out_dir_var.get())
        video_out = str(out_dir / f"doc_video_{ts}.mp4")
        self._log("🎬 Starting video generation…")
        cmd = [sys.executable, str(MAKE_VIDEO_PY),
               "--audio",      audio_path,
               "--screenshot", png_path,
               "--output",     video_out]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                self._log(f"  {line.rstrip()}")
            proc.wait()
            if proc.returncode == 0:
                self._log(f"✅ Video saved: {video_out}", ACCENT_GRN)
                name_a = pathlib.Path(audio_path).name
                name_v = pathlib.Path(video_out).name
                self.after(0, lambda: self.save_lbl.config(
                    text=f"✓ {name_a}  +  {name_v}"))
            else:
                self._log("❌ make_doc_video.py exited with an error", ACCENT_RED)
        except Exception as e:
            self._log(f"❌ Video generation failed: {e}", ACCENT_RED)


# ─── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
