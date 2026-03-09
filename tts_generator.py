#!/usr/bin/env python3
"""
TTS Generator — Accessibility Video Creator
--------------------------------------------
Desktop app for generating TTS audio (ElevenLabs or Google Cloud TTS)
and optionally combining it with a scrolling PNG screenshot into an MP4.

Requirements:
    pip install requests

Built-in to Python 3:  tkinter, json, threading, subprocess, pathlib
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import base64
import pathlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ─── Config / persistence ─────────────────────────────────────────────────────
SCRIPT_DIR   = pathlib.Path(__file__).parent
CONFIG_FILE  = SCRIPT_DIR / "tts_generator_config.json"
NARRATION_TXT = SCRIPT_DIR / "example" / "HOW_IT_WORKS_narration.txt"
SCREENSHOT_PNG = SCRIPT_DIR / "HOW_IT_WORKS_screenshot.png"
MAKE_VIDEO_PY  = SCRIPT_DIR / "make_doc_video.py"

DEFAULT_CFG = {
    "service":        "elevenlabs",
    "el_api_key":     "",
    "el_voice_id":    "21m00Tcm4TlvDq8ikWAM",   # Rachel
    "google_api_key": "",
    "google_voice":   "en-US-Neural2-F",
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


# ─── TTS API calls ─────────────────────────────────────────────────────────────
def _sanitize_key(key: str) -> str:
    return "".join(c for c in key if 0x20 <= ord(c) <= 0x7E).strip()


def synthesize_elevenlabs(text: str, api_key: str, voice_id: str, out_path: str,
                           log_fn=None):
    """Call ElevenLabs API and write MP3 to out_path."""
    import urllib.request, json as _json
    key = _sanitize_key(api_key)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = _json.dumps({
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Accept", "audio/mpeg")
    req.add_header("Content-Type", "application/json")

    if log_fn: log_fn("Calling ElevenLabs API...")
    with urllib.request.urlopen(req) as resp:
        audio_bytes = resp.read()

    if log_fn: log_fn(f"  Received {len(audio_bytes):,} bytes of audio")
    with open(out_path, "wb") as f:
        f.write(audio_bytes)


def synthesize_google(text: str, api_key: str, voice_name: str, out_path: str,
                      log_fn=None):
    """Call Google Cloud TTS API and write MP3 to out_path."""
    import urllib.request, json as _json
    lang_code = "-".join(voice_name.split("-")[:2])  # e.g. "en-US"
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    payload = _json.dumps({
        "input": {"text": text},
        "voice": {"languageCode": lang_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3"}
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    if log_fn: log_fn("Calling Google Cloud TTS API...")
    with urllib.request.urlopen(req) as resp:
        data = _json.loads(resp.read())

    audio_bytes = base64.b64decode(data["audioContent"])
    if log_fn: log_fn(f"  Received {len(audio_bytes):,} bytes of audio")
    with open(out_path, "wb") as f:
        f.write(audio_bytes)


# ─── GUI ──────────────────────────────────────────────────────────────────────
BG          = "#0d1117"
BG2         = "#161b22"
BORDER      = "#30363d"
FG          = "#c9d1d9"
FG_BRIGHT   = "#e6edf3"
ACCENT_BLUE = "#58a6ff"
ACCENT_GRN  = "#3fb950"
ACCENT_RED  = "#f85149"
ACCENT_ORG  = "#d29922"
FONT_UI     = ("Segoe UI", 10)
FONT_MONO   = ("Consolas", 9)
FONT_H1     = ("Segoe UI", 13, "bold")
FONT_H2     = ("Segoe UI", 11, "bold")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TTS Generator — Accessibility Video Creator")
        self.configure(bg=BG)
        self.geometry("860x780")
        self.minsize(700, 600)
        self.resizable(True, True)

        self.cfg = load_cfg()
        self._build_ui()
        self._load_narration_if_exists()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Top bar ───────────────────────────────────────────────────────────
        top = tk.Frame(self, bg=BG, padx=16, pady=10)
        top.pack(fill="x")
        tk.Label(top, text="TTS Generator", font=FONT_H1,
                 bg=BG, fg=FG_BRIGHT).pack(side="left")
        tk.Label(top, text=" · Accessibility Video Creator",
                 font=("Segoe UI", 10), bg=BG, fg="#8b949e").pack(side="left")

        # ── Service selector ─────────────────────────────────────────────────
        svc_frame = tk.LabelFrame(self, text=" TTS Service ", font=FONT_UI,
                                  bg=BG, fg="#8b949e", bd=1,
                                  relief="solid", padx=12, pady=10)
        svc_frame.pack(fill="x", padx=16, pady=(0, 8))

        self.svc_var = tk.StringVar(value=self.cfg["service"])
        for val, label in [("elevenlabs", "ElevenLabs"), ("google", "Google Cloud TTS")]:
            tk.Radiobutton(svc_frame, text=label, variable=self.svc_var,
                           value=val, command=self._on_service_change,
                           bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                           font=FONT_UI).pack(side="left", padx=12)

        # ── API settings ─────────────────────────────────────────────────────
        self.settings_frame = tk.LabelFrame(self, text=" API Settings ", font=FONT_UI,
                                             bg=BG, fg="#8b949e", bd=1,
                                             relief="solid", padx=12, pady=10)
        self.settings_frame.pack(fill="x", padx=16, pady=(0, 8))
        self._build_settings()

        # ── Text input area ──────────────────────────────────────────────────
        txt_frame = tk.LabelFrame(self, text=" Text to Speak ", font=FONT_UI,
                                   bg=BG, fg="#8b949e", bd=1, relief="solid",
                                   padx=12, pady=8)
        txt_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        btn_row = tk.Frame(txt_frame, bg=BG)
        btn_row.pack(fill="x", pady=(0, 6))
        self._btn(btn_row, "📂 Load from file…", self._load_file, "#21262d", FG).pack(side="left")
        self._btn(btn_row, "📄 Load narration.txt", self._load_narration, "#21262d", FG).pack(side="left", padx=6)
        self._btn(btn_row, "🗑 Clear", self._clear_text, "#21262d", ACCENT_RED).pack(side="right")
        self.char_lbl = tk.Label(btn_row, text="0 chars", font=("Segoe UI", 9),
                                 bg=BG, fg="#8b949e")
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
        tk.Label(r1, text="Save to folder:", font=FONT_UI, bg=BG, fg=FG).pack(side="left")
        self.out_dir_var = tk.StringVar(value=self.cfg["output_dir"])
        tk.Entry(r1, textvariable=self.out_dir_var, bg=BG2, fg=FG,
                 insertbackground=FG, font=FONT_MONO, relief="flat",
                 bd=1).pack(side="left", fill="x", expand=True, padx=8)
        self._btn(r1, "Browse…", self._browse_output, "#21262d", FG).pack(side="left")

        # Image scroll (video mode)
        r2 = tk.Frame(out_frame, bg=BG)
        r2.pack(fill="x", pady=(8, 0))
        self.make_video_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="Also make scrolling video  (needs ffmpeg + screenshot PNG)",
                       variable=self.make_video_var, command=self._on_video_toggle,
                       bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                       font=FONT_UI).pack(side="left")

        self.video_row = tk.Frame(out_frame, bg=BG)
        tk.Label(self.video_row, text="Screenshot PNG:", font=FONT_UI,
                 bg=BG, fg=FG).pack(side="left")
        self.png_path_var = tk.StringVar(value=str(SCREENSHOT_PNG))
        tk.Entry(self.video_row, textvariable=self.png_path_var, bg=BG2, fg=FG,
                 insertbackground=FG, font=FONT_MONO, relief="flat",
                 bd=1).pack(side="left", fill="x", expand=True, padx=8)
        self._btn(self.video_row, "Browse…", self._browse_png, "#21262d", FG).pack(side="left")

        # ── Action buttons ───────────────────────────────────────────────────
        act_frame = tk.Frame(self, bg=BG, padx=16, pady=8)
        act_frame.pack(fill="x")

        self.gen_btn = self._btn(act_frame, "🎙  Generate Audio",
                                  self._on_generate, "#1f6feb", FG_BRIGHT,
                                  font=("Segoe UI", 11, "bold"), padx=20, pady=8)
        self.gen_btn.pack(side="left")

        self.open_btn = self._btn(act_frame, "▶  Open Audio",
                                   self._open_last_audio, "#21262d", FG,
                                   font=FONT_UI, padx=12, pady=8)
        self.open_btn.pack(side="left", padx=8)
        self.open_btn.config(state="disabled")

        self.save_lbl = tk.Label(act_frame, text="", font=("Segoe UI", 9),
                                  bg=BG, fg=ACCENT_GRN)
        self.save_lbl.pack(side="left", padx=8)

        # ── Log / progress area ──────────────────────────────────────────────
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

    def _btn(self, parent, text, cmd, bg, fg,
             font=None, padx=10, pady=4, **kw):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg=fg, activebackground=BORDER,
                      activeforeground=FG_BRIGHT, relief="flat", bd=0, cursor="hand2",
                      font=font or FONT_UI, padx=padx, pady=pady, **kw)
        b.bind("<Enter>", lambda e: b.config(bg=BORDER))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def _build_settings(self):
        """(Re)build the settings section based on selected service."""
        for w in self.settings_frame.winfo_children():
            w.destroy()

        svc = self.svc_var.get()
        if svc == "elevenlabs":
            self._lbl_entry(self.settings_frame, "API Key (xi-api-key):",
                            self.cfg.get("el_api_key", ""), "el_api_key", show="*")
            self._lbl_entry(self.settings_frame, "Voice ID:",
                            self.cfg.get("el_voice_id", DEFAULT_CFG["el_voice_id"]),
                            "el_voice_id")
            tk.Label(self.settings_frame,
                     text="Default voice: Rachel (21m00Tcm4TlvDq8ikWAM)  ·  "
                          "Browse voices at elevenlabs.io/voice-library",
                     font=("Segoe UI", 8), bg=BG, fg="#8b949e").pack(anchor="w")
        else:
            self._lbl_entry(self.settings_frame, "API Key:",
                            self.cfg.get("google_api_key", ""), "google_api_key", show="*")
            self._lbl_entry(self.settings_frame, "Voice Name:",
                            self.cfg.get("google_voice", DEFAULT_CFG["google_voice"]),
                            "google_voice")
            tk.Label(self.settings_frame,
                     text="Get key at console.cloud.google.com  ·  "
                          "Voices: en-US-Neural2-A … F, en-US-Studio-O, etc.",
                     font=("Segoe UI", 8), bg=BG, fg="#8b949e").pack(anchor="w")

    def _lbl_entry(self, parent, label, value, key, show=None):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, font=FONT_UI, bg=BG, fg=FG,
                 width=22, anchor="w").pack(side="left")
        var = tk.StringVar(value=value)
        e = tk.Entry(row, textvariable=var, bg=BG2, fg=FG, insertbackground=FG,
                     font=FONT_MONO, relief="flat", bd=1, show=show or "")
        e.pack(side="left", fill="x", expand=True)

        def _trace(*_):
            self.cfg[key] = var.get()
            save_cfg(self.cfg)
        var.trace_add("write", _trace)

    # ── Event handlers ───────────────────────────────────────────────────────
    def _on_service_change(self):
        self.cfg["service"] = self.svc_var.get()
        save_cfg(self.cfg)
        self._build_settings()

    def _on_video_toggle(self):
        if self.make_video_var.get():
            self.video_row.pack(fill="x", pady=(6, 0))
        else:
            self.video_row.pack_forget()

    def _on_text_change(self, _event=None):
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
            self._log(f"Loaded narration from {NARRATION_TXT}")
        else:
            messagebox.showwarning("Not found",
                f"Could not find:\n{NARRATION_TXT}\n\nPaste your text manually.")

    def _load_narration_if_exists(self):
        """Auto-load narration text on startup if text box is empty."""
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
        if hasattr(self, "_last_audio_path") and self._last_audio_path:
            os.startfile(self._last_audio_path)

    # ── Logging ──────────────────────────────────────────────────────────────
    def _log(self, msg: str, color=None):
        def _do():
            self.log_box.config(state="normal")
            ts = time.strftime("%H:%M:%S")
            self.log_box.insert("end", f"[{ts}] {msg}\n")
            if color:
                idx = self.log_box.index("end-2l")
                end = self.log_box.index("end-1c")
                self.log_box.tag_add(f"col_{id(msg)}", idx, end)
                self.log_box.tag_config(f"col_{id(msg)}", foreground=color)
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _do)

    # ── Generate ─────────────────────────────────────────────────────────────
    def _on_generate(self):
        text = self.text_box.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("No text", "Please enter some text first.")
            return
        svc = self.svc_var.get()
        if svc == "elevenlabs" and not self.cfg.get("el_api_key"):
            messagebox.showwarning("No API key", "Please enter your ElevenLabs API key.")
            return
        if svc == "google" and not self.cfg.get("google_api_key"):
            messagebox.showwarning("No API key", "Please enter your Google Cloud TTS API key.")
            return

        self.gen_btn.config(state="disabled", text="⏳  Generating…")
        self.save_lbl.config(text="")

        threading.Thread(target=self._generate_worker,
                         args=(text,), daemon=True).start()

    def _generate_worker(self, text: str):
        try:
            out_dir = pathlib.Path(self.out_dir_var.get())
            out_dir.mkdir(parents=True, exist_ok=True)
            ts       = time.strftime("%Y%m%d_%H%M%S")
            audio_out = str(out_dir / f"tts_audio_{ts}.mp3")

            svc = self.svc_var.get()
            if svc == "elevenlabs":
                synthesize_elevenlabs(
                    text, self.cfg["el_api_key"], self.cfg["el_voice_id"],
                    audio_out, log_fn=self._log
                )
            else:
                synthesize_google(
                    text, self.cfg["google_api_key"], self.cfg["google_voice"],
                    audio_out, log_fn=self._log
                )

            self._last_audio_path = audio_out
            size_kb = os.path.getsize(audio_out) // 1024
            self._log(f"✅ Audio saved: {audio_out}  ({size_kb} KB)", ACCENT_GRN)
            self.after(0, lambda: self.save_lbl.config(
                text=f"✓ {pathlib.Path(audio_out).name}"))
            self.after(0, lambda: self.open_btn.config(state="normal"))

            # Optionally make video
            if self.make_video_var.get():
                self._make_video_worker(audio_out, ts)

        except Exception as e:
            self._log(f"❌ Error: {e}", ACCENT_RED)
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
        finally:
            self.after(0, lambda: self.gen_btn.config(
                state="normal", text="🎙  Generate Audio"))

    def _make_video_worker(self, audio_path: str, ts: str):
        """Run make_doc_video.py as a subprocess."""
        if not MAKE_VIDEO_PY.exists():
            self._log(f"⚠  make_doc_video.py not found at {MAKE_VIDEO_PY}", ACCENT_ORG)
            return

        png_path = self.png_path_var.get().strip()
        if not pathlib.Path(png_path).exists():
            self._log(f"⚠  Screenshot PNG not found: {png_path}", ACCENT_ORG)
            return

        out_dir  = pathlib.Path(self.out_dir_var.get())
        video_out = str(out_dir / f"doc_video_{ts}.mp4")

        self._log("🎬 Starting video generation…")
        cmd = [
            sys.executable, str(MAKE_VIDEO_PY),
            "--audio",      audio_path,
            "--screenshot", png_path,
            "--output",     video_out,
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            for line in proc.stdout:
                self._log(f"  {line.rstrip()}")
            proc.wait()
            if proc.returncode == 0:
                self._log(f"✅ Video saved: {video_out}", ACCENT_GRN)
                self.after(0, lambda: self.save_lbl.config(
                    text=f"✓ {pathlib.Path(audio_path).name}  +  {pathlib.Path(video_out).name}"))
            else:
                self._log("❌ make_doc_video.py exited with error", ACCENT_RED)
        except Exception as e:
            self._log(f"❌ Video generation failed: {e}", ACCENT_RED)


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Check for requests (only needed for older Python without built-in urllib fixes)
    app = App()
    app.mainloop()
